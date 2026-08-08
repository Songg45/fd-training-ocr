"""Local PySide6 desktop front end for one-form OCR and review."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile

from .config import load_config
from .gui_controller import (EVENT_SELECTIONS, GuiPaths, apply_event_selection, apply_gui_edit,
                             automatic_export, build_processor, effective_event_selection,
                             export_record, load_gui_state, process_pdf, save_gui_state,
                             discover_pdfs, index_after_removal, structured_rows,
                             roster_table_rows, save_roster_table, unprocessed_sources,
                             validate_pdfs)
from .pdf_render import render_pdf


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        raise RuntimeError("PySide6 is required; install with: python -m pip install -e .[gui]") from exc
    return QtCore, QtGui, QtWidgets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fd-training-ocr-gui")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--pdftoppm", type=Path)
    parser.add_argument("--export-dir", type=Path, default=Path(r"C:\Temp\Exported"))
    parser.add_argument("--state-file", type=Path,
                        default=Path(r"C:\Temp\fd-training-ocr-gui-state.json"))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        paths = GuiPaths(args.master, args.template,
                         args.output_dir or config.output_dir / "gui", args.pdftoppm)
        QtCore, QtGui, QtWidgets = _qt()
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    class Preview(QtWidgets.QGraphicsView):
        def __init__(self):
            super().__init__()
            self.setScene(QtWidgets.QGraphicsScene(self))
            self.setDragMode(self.DragMode.ScrollHandDrag)
            self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)

        def show_image(self, image_path):
            self.scene().clear()
            pixmap = QtGui.QPixmap(str(image_path))
            self.scene().addPixmap(pixmap)
            self.scene().setSceneRect(pixmap.rect())
            self.fitInView(self.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

        def wheelEvent(self, event):
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            self.scale(factor, factor)

    class Window(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.sources = []
            self.current_index = -1
            self.source = None
            self.record = None
            self.records = {}
            self.failures = {}
            self.preview_paths = {}
            self.preview_temp = tempfile.TemporaryDirectory(prefix="fd-training-ocr-gui-")
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fd-ocr")
            self.future: Future | None = None
            self.processing_source = None
            self.batch_queue = []
            self.batch_total = 0
            self.batch_completed = 0
            self.batch_failures = 0
            self.stop_requested = False
            self.busy = False
            self.poll_timer = QtCore.QTimer(self)
            self.poll_timer.setInterval(100)
            self.poll_timer.timeout.connect(self.poll_result)
            self.setWindowTitle("FD Training OCR")
            self.resize(1350, 850)
            central = QtWidgets.QWidget(); self.setCentralWidget(central)
            layout = QtWidgets.QVBoxLayout(central)
            controls = QtWidgets.QHBoxLayout(); layout.addLayout(controls)
            self.load_button = QtWidgets.QPushButton("Add PDFs")
            self.folder_button = QtWidgets.QPushButton("Add Folder")
            self.roster_button = QtWidgets.QPushButton("Roster")
            self.remove_button = QtWidgets.QPushButton("Remove PDF")
            self.previous_button = QtWidgets.QPushButton("Previous")
            self.next_button = QtWidgets.QPushButton("Next")
            self.page_label = QtWidgets.QLabel("0 of 0")
            self.process_button = QtWidgets.QPushButton("Process")
            self.process_all_button = QtWidgets.QPushButton("Process All")
            self.stop_button = QtWidgets.QPushButton("Stop After Current")
            self.export_button = QtWidgets.QPushButton("Export Results")
            self.remove_button.setEnabled(False)
            self.previous_button.setEnabled(False); self.next_button.setEnabled(False)
            self.process_button.setEnabled(False); self.process_all_button.setEnabled(False)
            self.stop_button.setEnabled(False); self.export_button.setEnabled(False)
            self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0)
            self.status = QtWidgets.QLabel("Load a PDF to begin")
            for widget in (self.load_button, self.folder_button, self.roster_button,
                           self.remove_button,
                           self.previous_button, self.page_label,
                           self.next_button, self.process_button, self.process_all_button,
                           self.stop_button, self.export_button,
                           self.progress, self.status): controls.addWidget(widget)
            self.warning = QtWidgets.QLabel("")
            self.warning.setStyleSheet("background:#8b1e1e;color:white;font-weight:bold;padding:8px;")
            self.warning.hide(); layout.addWidget(self.warning)
            splitter = QtWidgets.QSplitter(); layout.addWidget(splitter, 1)
            self.preview = Preview(); splitter.addWidget(self.preview)
            tabs = QtWidgets.QTabWidget(); splitter.addWidget(tabs); splitter.setSizes([700, 650])
            self.table = QtWidgets.QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["Field", "Result (editable)", "Warnings"])
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
                                       QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed)
            self.table.itemChanged.connect(self.result_edited)
            self.table.cellDoubleClicked.connect(self.summary_activated)
            tabs.addTab(self.table, "Structured Results")
            self.raw = QtWidgets.QPlainTextEdit(); self.raw.setReadOnly(True)
            tabs.addTab(self.raw, "Raw JSON")
            self.load_button.clicked.connect(self.load_pdfs)
            self.folder_button.clicked.connect(self.load_folder)
            self.roster_button.clicked.connect(self.show_roster)
            self.remove_button.clicked.connect(self.remove_current_pdf)
            self.previous_button.clicked.connect(lambda: self.navigate(-1))
            self.next_button.clicked.connect(lambda: self.navigate(1))
            self.process_button.clicked.connect(self.process)
            self.process_all_button.clicked.connect(self.process_all)
            self.stop_button.clicked.connect(self.request_stop)
            self.export_button.clicked.connect(self.export)
            self.restore_state()

        def restore_state(self):
            try:
                sources, current_index, records, failures = load_gui_state(args.state_file)
                self.sources = sources
                self.current_index = current_index
                self.records = records
                self.failures = failures
                if self.current_index >= 0:
                    self.show_current()
                    self.status.setText(
                        f"Restored {len(self.sources)} queued PDF"
                        f"{'s' if len(self.sources) != 1 else ''}")
                else:
                    self.update_navigation()
            except (OSError, ValueError) as exc:
                self.status.setText(f"Unable to restore queue: {exc}")

        def persist_state(self):
            try:
                save_gui_state(args.state_file, self.sources, self.current_index,
                               self.records, self.failures)
            except OSError as exc:
                self.status.setText(f"Unable to save queue state: {exc}")

        def show_roster(self):
            roster_path = config.roster_path
            if roster_path is None:
                QtWidgets.QMessageBox.critical(
                    self, "Roster unavailable", "Configure an external roster_path first.")
                return
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle(f"Roster — {roster_path}")
            dialog.resize(900, 650)
            layout = QtWidgets.QVBoxLayout(dialog)
            path_label = QtWidgets.QLabel(f"Current roster: {roster_path}")
            path_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(path_label)
            table = QtWidgets.QTableWidget(0, 3)
            table.setHorizontalHeaderLabels(["Name", "Unit IDs (comma-separated)",
                                              "Aliases (comma-separated)"])
            table.horizontalHeader().setSectionResizeMode(
                QtWidgets.QHeaderView.ResizeMode.Stretch)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            layout.addWidget(table, 1)

            def put_rows(rows):
                table.setRowCount(len(rows))
                for row_number, values in enumerate(rows):
                    for column, value in enumerate(values):
                        table.setItem(row_number, column, QtWidgets.QTableWidgetItem(value))

            try:
                put_rows(roster_table_rows(roster_path, Path.cwd()))
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(dialog, "Unable to read roster", str(exc))

            controls = QtWidgets.QHBoxLayout(); layout.addLayout(controls)
            import_button = QtWidgets.QPushButton("Import Roster…")
            add_button = QtWidgets.QPushButton("Add Row")
            remove_button = QtWidgets.QPushButton("Remove Selected")
            save_button = QtWidgets.QPushButton("Save Roster")
            close_button = QtWidgets.QPushButton("Close")
            for button in (import_button, add_button, remove_button, save_button, close_button):
                controls.addWidget(button)

            def import_roster():
                name, _ = QtWidgets.QFileDialog.getOpenFileName(
                    dialog, "Import roster", "", "Roster JSON (*.json)")
                if not name:
                    return
                try:
                    put_rows(roster_table_rows(Path(name), Path.cwd()))
                    path_label.setText(
                        f"Imported for review: {name}\nSave destination: {roster_path}")
                except (OSError, ValueError) as exc:
                    QtWidgets.QMessageBox.critical(dialog, "Invalid roster", str(exc))

            def add_row():
                row = table.rowCount(); table.insertRow(row)
                for column in range(3):
                    table.setItem(row, column, QtWidgets.QTableWidgetItem(""))
                table.setCurrentCell(row, 0); table.editItem(table.item(row, 0))

            def remove_rows():
                selected = sorted({index.row() for index in table.selectionModel().selectedRows()},
                                  reverse=True)
                for row in selected:
                    table.removeRow(row)

            def save_roster():
                rows = []
                for row in range(table.rowCount()):
                    rows.append(tuple(
                        table.item(row, column).text() if table.item(row, column) else ""
                        for column in range(3)))
                try:
                    destination = save_roster_table(roster_path, Path.cwd(), rows)
                    path_label.setText(f"Current roster: {destination}")
                    self.status.setText(f"Saved roster with {len(rows)} table rows")
                    QtWidgets.QMessageBox.information(
                        dialog, "Roster saved", "The updated roster will be used by the next OCR run.")
                except (OSError, ValueError) as exc:
                    QtWidgets.QMessageBox.critical(dialog, "Unable to save roster", str(exc))

            import_button.clicked.connect(import_roster)
            add_button.clicked.connect(add_row)
            remove_button.clicked.connect(remove_rows)
            save_button.clicked.connect(save_roster)
            close_button.clicked.connect(dialog.accept)
            dialog.exec()

        def load_pdfs(self):
            names, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add training forms", "", "PDF files (*.pdf)")
            if not names: return
            try:
                selected = validate_pdfs([Path(name) for name in names])
                first_new = None
                for source in selected:
                    if source not in self.sources:
                        self.sources.append(source)
                        if first_new is None:
                            first_new = source
                if first_new is not None:
                    self.current_index = self.sources.index(first_new)
                elif self.current_index < 0:
                    self.current_index = 0
                self.show_current()
                self.persist_state()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to load PDF", str(exc))

        def load_folder(self):
            name = QtWidgets.QFileDialog.getExistingDirectory(self, "Add folder of training forms")
            if not name:
                return
            try:
                discovered = discover_pdfs(Path(name))
                if not discovered:
                    QtWidgets.QMessageBox.information(
                        self, "No PDFs found", "The selected folder contains no PDF files.")
                    return
                existing = set(self.sources)
                additions = [source for source in discovered if source not in existing]
                skipped = len(discovered) - len(additions)
                first_new = additions[0] if additions else None
                self.sources.extend(additions)
                if first_new is not None:
                    self.current_index = self.sources.index(first_new)
                    self.show_current()
                elif self.current_index < 0:
                    self.current_index = 0
                    self.show_current()
                self.status.setText(
                    f"Added {len(additions)} PDF{'s' if len(additions) != 1 else ''} from folder; "
                    f"skipped {skipped} duplicate{'s' if skipped != 1 else ''}")
                self.update_navigation()
                self.persist_state()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to load folder", str(exc))

        def navigate(self, offset):
            target = self.current_index + offset
            if 0 <= target < len(self.sources):
                self.current_index = target
                try:
                    self.show_current()
                    self.persist_state()
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(self, "Unable to load PDF", str(exc))

        def remove_current_pdf(self):
            if self.busy or not (0 <= self.current_index < len(self.sources)):
                return
            source = self.sources.pop(self.current_index)
            self.records.pop(source, None)
            self.failures.pop(source, None)
            self.preview_paths.pop(source, None)
            self.current_index = index_after_removal(self.current_index, len(self.sources))
            if self.current_index >= 0:
                self.show_current()
                self.status.setText(f"Removed {source.name} from queue")
            else:
                self.source = None
                self.record = None
                self.preview.scene().clear()
                self.raw.clear()
                self.table.setRowCount(0)
                self.warning.hide()
                self.status.setText("Queue empty — load a PDF to begin")
                self.update_navigation()
            self.persist_state()

        def show_current(self):
            self.source = self.sources[self.current_index]
            image_path = self.preview_paths.get(self.source)
            if image_path is None:
                preview_dir = Path(self.preview_temp.name) / f"document-{self.current_index + 1}"
                pages = render_pdf(self.source, preview_dir, dpi=300, pdftoppm=paths.pdftoppm)
                image_path = pages[0].path
                self.preview_paths[self.source] = image_path
            self.preview.show_image(image_path)
            self.record = self.records.get(self.source)
            if self.record is None:
                self.raw.clear(); self.table.setRowCount(0)
                failure = self.failures.get(self.source)
                if failure:
                    self.warning.setText(f"PROCESSING FAILED — {failure}")
                    self.warning.show()
                    self.status.setText("Processing failed — retry with Process or Process All")
                else:
                    self.warning.hide()
                    self.status.setText(self.source.name)
            else:
                self.display_record(self.record)
            self.update_navigation()

        def update_navigation(self):
            count = len(self.sources)
            position = self.current_index + 1 if count else 0
            self.page_label.setText(f"{position} of {count}" +
                                    (f" — {self.source.name}" if self.source else ""))
            self.previous_button.setEnabled(not self.busy and self.current_index > 0)
            self.next_button.setEnabled(not self.busy and 0 <= self.current_index < count - 1)
            self.remove_button.setEnabled(not self.busy and self.source is not None)
            self.process_button.setEnabled(not self.busy and self.source is not None)
            self.process_all_button.setEnabled(
                not self.busy and bool(unprocessed_sources(self.sources, self.records)))
            self.stop_button.setEnabled(
                self.busy and self.batch_total > 0 and not self.stop_requested)
            self.export_button.setEnabled(not self.busy and self.record is not None)

        def process(self):
            self.set_busy(True); self.status.setText("Processing locally: Stages 1–2 Qwen2.5-VL, Stage 3 Qwen3-VL…")
            source = self.source
            if source is None:
                return
            self.batch_queue = []
            self.batch_total = 0
            self.stop_requested = False
            self.progress.setRange(0, 0)
            self.processing_source = source
            self.future = self.executor.submit(
                lambda: process_pdf(source, build_processor(config, paths)))
            self.poll_timer.start()

        def process_all(self):
            pending = list(unprocessed_sources(self.sources, self.records))
            if not pending:
                self.status.setText("All queued PDFs are already processed")
                return
            self.batch_queue = pending
            self.batch_total = len(pending)
            self.batch_completed = 0
            self.batch_failures = 0
            self.stop_requested = False
            self.set_busy(True)
            self.progress.setRange(0, self.batch_total)
            self.progress.setValue(0)
            self.process_next_batch_item()

        def process_next_batch_item(self):
            if self.stop_requested or not self.batch_queue:
                self.finish_batch(stopped=self.stop_requested)
                return
            source = self.batch_queue.pop(0)
            self.current_index = self.sources.index(source)
            self.show_current()
            self.processing_source = source
            self.status.setText(
                f"Processing {self.batch_completed + 1} of {self.batch_total}: {source.name}")
            self.future = self.executor.submit(
                lambda: process_pdf(source, build_processor(config, paths)))
            self.poll_timer.start()

        def request_stop(self):
            if self.busy and self.batch_total:
                self.stop_requested = True
                self.stop_button.setEnabled(False)
                self.status.setText("Stop requested — finishing the current PDF")

        @QtCore.Slot()
        def poll_result(self):
            if self.future is None or not self.future.done():
                return
            self.poll_timer.stop()
            future, self.future = self.future, None
            try:
                self.finished(self.processing_source, future.result())
            except Exception as exc:
                self.failed(self.processing_source, f"{type(exc).__name__}: {exc}")

        def set_busy(self, busy):
            self.busy = busy
            self.load_button.setEnabled(not busy)
            self.folder_button.setEnabled(not busy)
            self.roster_button.setEnabled(not busy)
            if not busy and not self.batch_total:
                self.progress.setRange(0, 1)
                self.progress.setValue(1)
            self.update_navigation()

        def finished(self, source, record):
            exported = automatic_export(record, args.export_dir)
            self.records[source] = record
            self.failures.pop(source, None)
            self.persist_state()
            self.record = record
            self.display_record(record)
            if self.batch_total:
                self.batch_completed += 1
                self.progress.setValue(self.batch_completed)
                self.process_next_batch_item()
            else:
                self.processing_source = None
                self.set_busy(False)
                self.status.setText(f"Complete — exported {exported.name}")

        def display_record(self, record):
            self.raw.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
            rows = structured_rows(record); self.table.blockSignals(True); self.table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                name, value, warnings, editable = values
                for column, text in enumerate((name, value, warnings)):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setToolTip(text)
                    if column == 1:
                        item.setData(QtCore.Qt.ItemDataRole.UserRole, name)
                    if column != 1 or not editable:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, column, item)
            self.table.blockSignals(False)
            needs_review = record.get("status") == "review_required"
            self.warning.setText("REVIEW REQUIRED — " + ("; ".join(record.get("warnings", ())) or "one or more fields require review"))
            self.warning.setVisible(needs_review); self.status.setText("Complete — review required" if needs_review else "Complete")

        def result_edited(self, item):
            if self.record is None or item.column() != 1:
                return
            field_name = item.data(QtCore.Qt.ItemDataRole.UserRole)
            if not field_name:
                return
            if field_name in EVENT_SELECTIONS:
                return
            try:
                apply_gui_edit(self.record, str(field_name), item.text())
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.raw.setPlainText(json.dumps(self.record, indent=2, ensure_ascii=False))
                self.status.setText(f"Edited {field_name}; automatic export updated")
                self.export_button.setEnabled(True)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to save correction", str(exc))

        def summary_activated(self, row, column):
            item = self.table.item(row, 1)
            selection_name = (item.data(QtCore.Qt.ItemDataRole.UserRole)
                              if item is not None else None)
            if self.record is None or selection_name not in EVENT_SELECTIONS:
                return
            event = self.record.get("event", {})
            labels, _, _ = EVENT_SELECTIONS[selection_name]
            selected = set(effective_event_selection(event, selection_name) or ())
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle(f"Select {selection_name}")
            layout = QtWidgets.QVBoxLayout(dialog)
            checks = {}
            for key, label in labels.items():
                check = QtWidgets.QCheckBox(label)
                check.setChecked(key in selected)
                checks[key] = check
                layout.addWidget(check)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Ok |
                QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            try:
                apply_event_selection(
                    self.record, selection_name,
                    [key for key, check in checks.items() if check.isChecked()])
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.display_record(self.record)
                self.status.setText(f"Edited {selection_name}; automatic export updated")
                self.export_button.setEnabled(True)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to save correction", str(exc))

        def failed(self, source, message):
            self.failures[source] = message
            self.persist_state()
            if self.batch_total:
                self.batch_completed += 1
                self.batch_failures += 1
                self.progress.setValue(self.batch_completed)
                self.process_next_batch_item()
            else:
                self.processing_source = None
                self.set_busy(False)
                self.status.setText("Processing failed")
                QtWidgets.QMessageBox.critical(self, "OCR failed", message)

        def finish_batch(self, stopped=False):
            total = self.batch_total
            completed = self.batch_completed
            failures = self.batch_failures
            self.batch_queue = []
            self.batch_total = 0
            self.processing_source = None
            self.stop_requested = False
            self.set_busy(False)
            self.progress.setRange(0, total or 1)
            self.progress.setValue(completed)
            outcome = "Stopped" if stopped else "Batch complete"
            self.status.setText(
                f"{outcome}: {completed} of {total} attempted; {failures} failed")
            self.persist_state()

        def export(self):
            if self.record is None: return
            suggested = str(paths.output_dir / f"{self.record['source_sha256']}.json")
            name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export results", suggested, "JSON files (*.json)")
            if name:
                try:
                    export_record(self.record, Path(name)); self.status.setText(f"Exported {Path(name).name}")
                except OSError as exc: QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))

        def closeEvent(self, event):
            if self.future is not None and not self.future.done():
                event.ignore(); QtWidgets.QMessageBox.information(self, "Processing", "Wait for local OCR to finish before closing."); return
            self.poll_timer.stop()
            self.persist_state()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.preview_temp.cleanup(); super().closeEvent(event)

    app = QtWidgets.QApplication(sys.argv[:1])
    window = Window(); window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
