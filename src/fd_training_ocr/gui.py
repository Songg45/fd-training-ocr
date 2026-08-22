"""Local PySide6 desktop front end for one-form OCR and review."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile

from .config import load_config
from .gui_controller import (EVENT_SELECTIONS, GuiPaths, accept_stage3_suggestion,
                             apply_event_selection, apply_gui_edit,
                             apply_roster_linked_unit_edit,
                             populate_name_from_roster_unit,
                             populate_unit_from_roster_name,
                             automatic_export, build_processor, effective_event_selection,
                             create_startup_backup,
                             load_gui_state, process_pdf, save_gui_state,
                             discover_pdfs, display_value, index_after_removal, structured_rows,
                             queue_index_for_page,
                             attendee_row_from_field, remove_attendee, roster_table_rows,
                             add_attendee, first_available_attendee_row, save_roster_table,
                             roster_linked_attendee_values,
                             stage3_suggestion, unprocessed_sources,
                             validate_pdfs, alignment_fallback_record)
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
    parser.add_argument("--backup-dir", type=Path,
                        default=Path(r"C:\Temp\FDTrainingOCR-Backups"))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        paths = GuiPaths(args.master, args.template,
                         args.output_dir or config.output_dir / "gui", args.pdftoppm)
        backup_warning = None
        try:
            create_startup_backup(
                backup_dir=args.backup_dir, export_dir=args.export_dir,
                state_file=args.state_file, config_file=args.config,
                roster_file=config.roster_path)
        except (OSError, ValueError) as exc:
            backup_warning = str(exc)
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
            self.load_button = QtGui.QAction("Add File(s)", self)
            self.folder_button = QtGui.QAction("Add Folder", self)
            self.roster_button = QtGui.QAction("Roster", self)
            self.remove_button = QtGui.QAction("Remove PDF", self)
            self.remove_all_button = QtGui.QAction("Remove All", self)
            self.previous_button = QtWidgets.QPushButton("Previous")
            self.next_button = QtWidgets.QPushButton("Next")
            self.goto_number = QtWidgets.QSpinBox()
            self.goto_number.setRange(1, 1)
            self.goto_number.setPrefix("PDF ")
            self.goto_number.setAccessibleName("PDF number")
            self.goto_number.setAccessibleDescription(
                "Enter the PDF number to open, then activate Go To")
            self.goto_button = QtWidgets.QPushButton("Go To")
            self.page_label = QtWidgets.QLabel("0 of 0")
            self.process_button = QtGui.QAction("Process Selected", self)
            self.process_all_button = QtGui.QAction("Process All", self)
            self.stop_button = QtWidgets.QPushButton("Stop After Current")
            self.delete_attendee_button = QtGui.QAction("Delete Attendee", self)
            self.add_attendee_button = QtGui.QAction("Add Attendee", self)
            self.add_attendee_voice_button = QtWidgets.QPushButton("Add Attendee")
            self.add_attendee_voice_button.setAccessibleName("Add Attendee")
            self.add_attendee_voice_button.setAccessibleDescription(
                "Open labeled inputs to add an attendee to this record")
            self.delete_attendee_voice_button = QtWidgets.QPushButton("Delete Attendee")
            self.delete_attendee_voice_button.setAccessibleName("Delete Attendee")
            self.delete_attendee_voice_button.setAccessibleDescription(
                "Delete the attendee whose Unit ID or Name field is selected")
            self.accept_stage3_button = QtWidgets.QPushButton("Accept Stage 3")

            def menu_tool(text, actions):
                tool = QtWidgets.QToolButton()
                tool.setText(text)
                tool.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
                menu = QtWidgets.QMenu(tool)
                for action in actions:
                    menu.addAction(action)
                tool.setMenu(menu)
                return tool

            self.add_menu_button = menu_tool(
                "Add", (self.load_button, self.folder_button, self.roster_button))
            self.remove_menu_button = menu_tool(
                "Remove", (self.remove_button, self.remove_all_button))
            self.process_menu_button = menu_tool(
                "Process", (self.process_button, self.process_all_button))
            self.attendees_menu_button = menu_tool(
                "Attendees", (self.add_attendee_button, self.delete_attendee_button))
            self.remove_button.setEnabled(False)
            self.remove_all_button.setEnabled(False)
            self.previous_button.setEnabled(False); self.next_button.setEnabled(False)
            self.goto_number.setEnabled(False); self.goto_button.setEnabled(False)
            self.process_button.setEnabled(False); self.process_all_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.delete_attendee_button.setEnabled(False)
            self.add_attendee_button.setEnabled(False)
            self.add_attendee_voice_button.setEnabled(False)
            self.delete_attendee_voice_button.setEnabled(False)
            self.accept_stage3_button.setEnabled(False)
            self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0)
            self.status = QtWidgets.QLabel("Load a PDF to begin")
            for widget in (self.add_menu_button, self.remove_menu_button,
                           self.process_menu_button, self.attendees_menu_button,
                           self.previous_button, self.page_label, self.next_button,
                           self.goto_number, self.goto_button,
                           self.stop_button,
                           self.add_attendee_voice_button, self.delete_attendee_voice_button,
                           self.accept_stage3_button,
                           self.progress, self.status): controls.addWidget(widget)
            self.warning = QtWidgets.QLabel("")
            self.warning.setStyleSheet("background:#8b1e1e;color:white;font-weight:bold;padding:8px;")
            self.warning.hide(); layout.addWidget(self.warning)
            splitter = QtWidgets.QSplitter(); layout.addWidget(splitter, 1)
            self.preview = Preview(); splitter.addWidget(self.preview)
            tabs = QtWidgets.QTabWidget(); splitter.addWidget(tabs); splitter.setSizes([700, 650])
            self.form_scroll = QtWidgets.QScrollArea()
            self.form_scroll.setWidgetResizable(True)
            self.form_scroll.setAccessibleName("Training record form")
            self.form_scroll.setAccessibleDescription(
                "Labeled editable training record fields for Windows Voice Access")
            self.form_widget = QtWidgets.QWidget()
            self.form_layout = QtWidgets.QGridLayout(self.form_widget)
            self.form_layout.setColumnStretch(1, 2)
            self.form_layout.setColumnStretch(2, 1)
            self.form_scroll.setWidget(self.form_widget)
            self.field_controls = {}
            self.focused_field_name = None
            tabs.addTab(self.form_scroll, "Structured Results")
            self.raw = QtWidgets.QPlainTextEdit(); self.raw.setReadOnly(True)
            tabs.addTab(self.raw, "Raw JSON")
            self.load_button.triggered.connect(self.load_pdfs)
            self.folder_button.triggered.connect(self.load_folder)
            self.roster_button.triggered.connect(self.show_roster)
            self.remove_button.triggered.connect(self.remove_current_pdf)
            self.remove_all_button.triggered.connect(self.remove_all_pdfs)
            self.previous_button.clicked.connect(lambda: self.navigate(-1))
            self.next_button.clicked.connect(lambda: self.navigate(1))
            self.goto_button.clicked.connect(self.go_to_pdf)
            self.goto_number.lineEdit().returnPressed.connect(self.go_to_pdf)
            self.process_button.triggered.connect(self.process)
            self.process_all_button.triggered.connect(self.process_all)
            self.stop_button.clicked.connect(self.request_stop)
            self.delete_attendee_button.triggered.connect(self.delete_selected_attendee)
            self.add_attendee_button.triggered.connect(self.add_attendee_dialog)
            self.add_attendee_voice_button.clicked.connect(self.add_attendee_dialog)
            self.delete_attendee_voice_button.clicked.connect(self.delete_selected_attendee)
            self.accept_stage3_button.clicked.connect(self.accept_selected_stage3)
            self.restore_state()

        def eventFilter(self, watched, event):
            field_name = watched.property("fieldName") if watched is not None else None
            if field_name and event.type() == QtCore.QEvent.Type.FocusIn:
                self.focused_field_name = str(field_name)
                self.update_selection_buttons()
            if (field_name and event.type() == QtCore.QEvent.Type.FocusOut
                    and isinstance(watched, QtWidgets.QPlainTextEdit)):
                self.save_field_control(str(field_name), watched.toPlainText())
            return super().eventFilter(watched, event)

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
                self.navigate_to(target)

        def navigate_to(self, target):
            try:
                if self.record is not None:
                    automatic_export(self.record, args.export_dir)
                self.current_index = target
                self.show_current()
                self.persist_state()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    self, "Unable to save or load PDF", str(exc))

        def go_to_pdf(self):
            if self.busy or not self.sources:
                return
            try:
                target = queue_index_for_page(self.goto_number.value(), len(self.sources))
                self.navigate_to(target)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "Unable to go to PDF", str(exc))

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
                self.clear_record_form()
                self.warning.hide()
                self.status.setText("Queue empty — load a PDF to begin")
                self.update_navigation()
            self.persist_state()

        def remove_all_pdfs(self):
            if self.busy or not self.sources:
                return
            answer = QtWidgets.QMessageBox.question(
                self, "Remove all PDFs",
                f"Remove all {len(self.sources)} PDFs from the GUI queue?\n\n"
                "Source PDFs and exported JSON files will not be deleted.")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self.sources.clear()
            self.records.clear()
            self.failures.clear()
            self.preview_paths.clear()
            self.current_index = -1
            self.source = None
            self.record = None
            self.preview.scene().clear()
            self.raw.clear()
            self.clear_record_form()
            self.warning.hide()
            self.status.setText("Queue cleared — source PDFs and exports were preserved")
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
                self.raw.clear(); self.clear_record_form()
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
            self.goto_number.setRange(1, max(1, count))
            if count:
                self.goto_number.setValue(self.current_index + 1)
            self.goto_number.setEnabled(not self.busy and count > 0)
            self.goto_button.setEnabled(not self.busy and count > 0)
            self.remove_button.setEnabled(not self.busy and self.source is not None)
            self.remove_all_button.setEnabled(not self.busy and bool(self.sources))
            self.process_button.setEnabled(not self.busy and self.source is not None)
            self.process_all_button.setEnabled(
                not self.busy and bool(unprocessed_sources(self.sources, self.records)))
            self.stop_button.setEnabled(
                self.busy and self.batch_total > 0 and not self.stop_requested)
            self.add_attendee_button.setEnabled(not self.busy and self.record is not None)
            self.add_attendee_voice_button.setEnabled(not self.busy and self.record is not None)
            self.update_selection_buttons()
            self.update_menu_buttons()

        def update_menu_buttons(self):
            groups = (
                (self.add_menu_button,
                 (self.load_button, self.folder_button, self.roster_button)),
                (self.remove_menu_button,
                 (self.remove_button, self.remove_all_button)),
                (self.process_menu_button,
                 (self.process_button, self.process_all_button)),
                (self.attendees_menu_button,
                 (self.add_attendee_button, self.delete_attendee_button)),
            )
            for tool, actions in groups:
                tool.setEnabled(any(action.isEnabled() for action in actions))

        def add_attendee_dialog(self):
            if self.record is None:
                return
            available = first_available_attendee_row(self.record)
            if available is None:
                QtWidgets.QMessageBox.information(
                    self, "Attendee rows full", "All 19 attendee rows are occupied.")
                return
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("Add Attendee")
            form = QtWidgets.QFormLayout(dialog)
            roster_combo = QtWidgets.QComboBox()
            roster_combo.addItem("Custom entry…", None)
            try:
                if config.roster_path is not None:
                    for name, unit_ids, aliases in roster_table_rows(config.roster_path, Path.cwd()):
                        for unit_id in [item.strip() for item in unit_ids.split(",") if item.strip()]:
                            roster_combo.addItem(f"{name} — {unit_id}", (name, unit_id))
            except (OSError, ValueError):
                pass
            row_box = QtWidgets.QSpinBox(); row_box.setRange(1, 19); row_box.setValue(available)
            unit_edit = QtWidgets.QLineEdit()
            name_edit = QtWidgets.QLineEdit()
            roster_combo.setAccessibleName("Roster member")
            row_box.setAccessibleName("Attendee row")
            row_box.setAccessibleDescription("Choose the numbered attendee row")
            unit_edit.setAccessibleName("Attendee Unit ID")
            unit_edit.setAccessibleDescription(
                "Enter a Unit ID. An exact roster match fills the attendee name")
            name_edit.setAccessibleName("Attendee Name")
            name_edit.setAccessibleDescription(
                "Enter a roster name or alias. A unique match fills the Unit ID")
            form.addRow("Roster", roster_combo)
            form.addRow("Form row", row_box)
            form.addRow("Unit ID", unit_edit)
            form.addRow("Print Name", name_edit)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Ok |
                QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            confirm_button = buttons.button(
                QtWidgets.QDialogButtonBox.StandardButton.Ok)
            confirm_button.setText("Add Attendee")
            confirm_button.setAccessibleName("Confirm Add Attendee")
            confirm_button.setAccessibleDescription(
                "Add the attendee using the entered row, Unit ID, and name")
            cancel_button = buttons.button(
                QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            cancel_button.setAccessibleName("Cancel Add Attendee")
            form.addRow(buttons)

            def roster_selected(index):
                selection = roster_combo.itemData(index)
                if selection:
                    name_edit.setText(selection[0]); unit_edit.setText(selection[1])

            def link_roster(changed):
                if config.roster_path is None:
                    return
                try:
                    unit_id, print_name = roster_linked_attendee_values(
                        unit_edit.text(), name_edit.text(), config.roster_path,
                        Path.cwd(), changed)
                    unit_edit.setText(unit_id)
                    name_edit.setText(print_name)
                except (OSError, ValueError):
                    pass

            roster_combo.currentIndexChanged.connect(roster_selected)
            # Voice Access may update a line edit without producing the same
            # focus transition as a mouse/keyboard edit. Link on user edits as
            # well as focus changes, then resolve once more on submission.
            unit_edit.textEdited.connect(lambda _value: link_roster("unit_id"))
            name_edit.textEdited.connect(lambda _value: link_roster("print_name"))
            unit_edit.editingFinished.connect(lambda: link_roster("unit_id"))
            name_edit.editingFinished.connect(lambda: link_roster("print_name"))
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
            unit_edit.setFocus()
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            try:
                # Prefer an exact Unit ID match; if the Unit ID is unknown,
                # allow an exact roster name/alias to provide its unique ID.
                original_unit = unit_edit.text()
                link_roster("unit_id")
                if unit_edit.text() == original_unit and not name_edit.text().strip() == "":
                    link_roster("print_name")
                add_attendee(self.record, row_box.value(), unit_edit.text(), name_edit.text())
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.display_record(self.record)
                self.status.setText(
                    f"Added attendee row {row_box.value()}; automatic export updated")
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to add attendee", str(exc))

        def selected_attendee_row(self):
            return attendee_row_from_field(self.focused_field_name or "")

        def update_attendee_button(self):
            enabled = (not self.busy and self.record is not None
                       and self.selected_attendee_row() is not None)
            self.delete_attendee_button.setEnabled(enabled)
            self.delete_attendee_voice_button.setEnabled(enabled)

        def selected_field_name(self):
            return self.focused_field_name

        def update_selection_buttons(self):
            self.update_attendee_button()
            field_name = self.selected_field_name()
            suggestion = (stage3_suggestion(self.record, field_name)
                          if self.record is not None and field_name else None)
            self.accept_stage3_button.setEnabled(not self.busy and suggestion is not None)
            self.update_menu_buttons()

        def accept_selected_stage3(self):
            field_name = self.selected_field_name()
            if self.record is None or not field_name:
                return
            try:
                suggestion = stage3_suggestion(self.record, field_name)
                accept_stage3_suggestion(self.record, field_name)
                roster_unit = None
                roster_name = None
                if (field_name.endswith(".print_name") and suggestion is not None
                        and config.roster_path is not None):
                    roster_unit = populate_unit_from_roster_name(
                        self.record, field_name, str(suggestion),
                        config.roster_path, Path.cwd())
                elif (field_name.endswith(".unit_id") and suggestion is not None
                      and config.roster_path is not None):
                    roster_name = populate_name_from_roster_unit(
                        self.record, field_name, str(suggestion),
                        config.roster_path, Path.cwd())
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.display_record(self.record)
                suffix = (f"; roster set Unit ID to {roster_unit}"
                          if roster_unit is not None else
                          (f"; roster set name to {roster_name}"
                           if roster_name is not None else ""))
                self.status.setText(
                    f"Accepted Stage 3 suggestion for {field_name}{suffix}; automatic export updated")
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(
                    self, "Unable to accept Stage 3 suggestion", str(exc))

        def delete_selected_attendee(self):
            attendee_row = self.selected_attendee_row()
            if self.record is None or attendee_row is None:
                return
            answer = QtWidgets.QMessageBox.question(
                self, "Delete attendee",
                f"Remove attendee row {attendee_row} from this OCR result?\n\n"
                "The scanned PDF will not be changed and machine evidence will remain in the audit trail.")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                remove_attendee(self.record, attendee_row)
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.display_record(self.record)
                self.status.setText(
                    f"Deleted attendee row {attendee_row}; automatic export updated")
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to delete attendee", str(exc))

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
                self.update_menu_buttons()
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
            self.build_record_form(structured_rows(record))
            needs_review = record.get("status") == "review_required"
            self.warning.setText("REVIEW REQUIRED — " + ("; ".join(record.get("warnings", ())) or "one or more fields require review"))
            self.warning.setVisible(needs_review); self.status.setText("Complete — review required" if needs_review else "Complete")
            self.update_selection_buttons()

        def clear_record_form(self):
            while self.form_layout.count():
                item = self.form_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.field_controls.clear()
            self.focused_field_name = None

        def build_record_form(self, rows):
            previous = self.focused_field_name
            self.clear_record_form()
            for column, text in enumerate(("Field", "Result", "Warnings")):
                self.form_layout.addWidget(QtWidgets.QLabel(f"<b>{text}</b>"), 0, column)
            first_control = None
            for row_number, (name, value, warnings, editable) in enumerate(rows, 1):
                field_label = name.replace("_", " ").replace(".", " ")
                label = QtWidgets.QLabel(field_label)
                self.form_layout.addWidget(label, row_number, 0)
                if name in EVENT_SELECTIONS:
                    control = QtWidgets.QPushButton(value)
                    control.clicked.connect(
                        lambda checked=False, field=name: self.edit_event_selection(field))
                elif editable and name == "description":
                    control = QtWidgets.QPlainTextEdit(value)
                    control.setMaximumHeight(90)
                elif editable:
                    control = QtWidgets.QLineEdit(value)
                    control.editingFinished.connect(
                        lambda field=name, edit=control: self.save_field_control(field, edit.text()))
                else:
                    control = QtWidgets.QLineEdit(value)
                    control.setReadOnly(True)
                control.setProperty("fieldName", name)
                control.setAccessibleName(field_label)
                control.setAccessibleDescription(
                    (f"Edit {field_label}. Changes save automatically."
                     if editable or name in EVENT_SELECTIONS else f"Read-only {field_label}."))
                control.installEventFilter(self)
                label.setBuddy(control)
                self.form_layout.addWidget(control, row_number, 1)
                warning = QtWidgets.QLabel(warnings)
                warning.setWordWrap(True)
                warning.setToolTip(warnings)
                warning.setAccessibleName(f"{field_label} warnings")
                self.form_layout.addWidget(warning, row_number, 2)
                self.field_controls[name] = control
                first_control = first_control or control
            controls = list(self.field_controls.values())
            for current, following in zip(controls, controls[1:]):
                self.setTabOrder(current, following)
            if previous in self.field_controls:
                self.focused_field_name = previous
            elif first_control is not None:
                self.focused_field_name = str(first_control.property("fieldName"))

        def save_field_control(self, field_name, value):
            if self.record is None or field_name in EVENT_SELECTIONS:
                return
            field = self.record.get("fields", {}).get(field_name)
            current = "" if field is None or display_value(field) is None else str(display_value(field))
            if value == current:
                return
            try:
                roster_message = None
                if (str(field_name).endswith(".unit_id")
                        and config.roster_path is not None):
                    canonical_name = apply_roster_linked_unit_edit(
                        self.record, str(field_name), value,
                        config.roster_path, Path.cwd())
                    if canonical_name is not None:
                        roster_message = f"roster set name to {canonical_name}"
                elif (str(field_name).endswith(".print_name")
                      and config.roster_path is not None):
                    apply_gui_edit(self.record, str(field_name), value)
                    roster_unit = populate_unit_from_roster_name(
                        self.record, str(field_name), value,
                        config.roster_path, Path.cwd())
                    if roster_unit is not None:
                        roster_message = f"roster set Unit ID to {roster_unit}"
                else:
                    apply_gui_edit(self.record, str(field_name), value)
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.display_record(self.record)
                suffix = f"; {roster_message}" if roster_message is not None else ""
                self.status.setText(
                    f"Edited {field_name}{suffix}; automatic export updated")
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to save correction", str(exc))

        def edit_event_selection(self, selection_name):
            if self.record is None or selection_name not in EVENT_SELECTIONS:
                return
            self.focused_field_name = selection_name
            self.update_selection_buttons()
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
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to save correction", str(exc))

        def failed(self, source, message):
            if source is not None and message.startswith("AlignmentError:"):
                record = alignment_fallback_record(source, message)
                automatic_export(record, args.export_dir)
                self.records[source] = record
                self.failures.pop(source, None)
                self.persist_state()
                if source == self.source:
                    self.record = record
                    self.display_record(record)
                if self.batch_total:
                    self.batch_completed += 1
                    self.batch_failures += 1
                    self.progress.setValue(self.batch_completed)
                    self.process_next_batch_item()
                else:
                    self.processing_source = None
                    self.set_busy(False)
                    self.status.setText("Alignment failed — manual field template created")
                return
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

        def closeEvent(self, event):
            if self.future is not None and not self.future.done():
                event.ignore(); QtWidgets.QMessageBox.information(self, "Processing", "Wait for local OCR to finish before closing."); return
            if self.record is not None:
                try:
                    automatic_export(self.record, args.export_dir)
                except OSError as exc:
                    event.ignore()
                    QtWidgets.QMessageBox.critical(self, "Unable to save current record", str(exc))
                    return
            self.poll_timer.stop()
            self.persist_state()
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.preview_temp.cleanup(); super().closeEvent(event)

    app = QtWidgets.QApplication(sys.argv[:1])
    window = Window(); window.show()
    if backup_warning:
        QtWidgets.QMessageBox.warning(
            window, "Startup backup failed",
            "The application opened, but its startup backup could not be created:\n\n"
            + backup_warning)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
