"""Local PySide6 desktop front end for one-form OCR and review."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
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
                             import_fireworks_staff_ids,
                             roster_linked_attendee_values,
                             stage3_suggestion, unprocessed_sources,
                             validate_pdfs, alignment_fallback_record)
from .pdf_render import render_pdf
from .fireworks import (displayed_fireworks_request, formatted_fireworks_request,
                        fireworks_staff_ids, load_fireworks_mappings,
                        save_fireworks_request_edit, selected_category_name,
                        selected_location_name,
                        update_payload_selection, validate_fireworks_payload)
from .fireworks_client import (DuplicateSubmissionError, FireworksClient,
                               FireworksConnectionError,
                               FireworksSubmissionRejected,
                               FireworksSubmissionUnknown, SubmissionLedger)
from .validation import load_roster


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
    parser.add_argument("--fireworks-ids", type=Path,
                        default=Path(r"C:\Temp\fireworks-category-ids.json"))
    parser.add_argument("--fireworks-ledger", type=Path,
                        default=Path(r"C:\Temp\FDTrainingOCR-Fireworks\submissions.jsonl"))
    parser.add_argument("--fireworks-api-base",
                        default="https://webtrainingapi.eprsys.com/api")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        paths = GuiPaths(args.master, args.template,
                         args.output_dir or config.output_dir / "gui", args.pdftoppm)
        fireworks_mapping_warning = None
        try:
            fireworks_mappings = load_fireworks_mappings(args.fireworks_ids)
        except (OSError, ValueError) as exc:
            fireworks_mappings = None
            fireworks_mapping_warning = str(exc)
        fireworks_ledger = SubmissionLedger(args.fireworks_ledger)
        backup_warning = None
        try:
            create_startup_backup(
                backup_dir=args.backup_dir, export_dir=args.export_dir,
                state_file=args.state_file, config_file=args.config,
                roster_file=config.roster_path,
                fireworks_mappings_file=args.fireworks_ids,
                fireworks_ledger_file=args.fireworks_ledger)
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
            self.fireworks_future: Future | None = None
            self.fireworks_operation = None
            self.fireworks_pending = None
            self.fireworks_client = None
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
            self.fireworks_timer = QtCore.QTimer(self)
            self.fireworks_timer.setInterval(100)
            self.fireworks_timer.timeout.connect(self.poll_fireworks_result)
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
            formatted_widget = QtWidgets.QWidget()
            formatted_layout = QtWidgets.QVBoxLayout(formatted_widget)
            fireworks_fields = QtWidgets.QGridLayout()
            self.fireworks_category = QtWidgets.QComboBox()
            self.fireworks_category.setAccessibleName("Fireworks Category")
            self.fireworks_category.addItem("Select category…", None)
            self.fireworks_location = QtWidgets.QComboBox()
            self.fireworks_location.setAccessibleName("Fireworks Location")
            self.fireworks_location.addItem("Select location…", None)
            if fireworks_mappings is not None:
                for item in fireworks_mappings.categories:
                    suffix = "" if item.status == "confirmed" else f" ({item.status})"
                    self.fireworks_category.addItem(item.name + suffix, item.name)
                for item in fireworks_mappings.locations:
                    self.fireworks_location.addItem(item.name, item.name)
                station_text = (
                    f"{fireworks_mappings.station_name} "
                    f"({fireworks_mappings.station_id})")
            else:
                station_text = "Mappings unavailable"
            self.fireworks_station = QtWidgets.QLabel(station_text)
            self.fireworks_station.setAccessibleName("Fireworks Station")
            fireworks_fields.addWidget(QtWidgets.QLabel("Category"), 0, 0)
            fireworks_fields.addWidget(self.fireworks_category, 0, 1)
            fireworks_fields.addWidget(QtWidgets.QLabel("Class Location"), 1, 0)
            fireworks_fields.addWidget(self.fireworks_location, 1, 1)
            fireworks_fields.addWidget(QtWidgets.QLabel("Station"), 2, 0)
            fireworks_fields.addWidget(self.fireworks_station, 2, 1)
            self.formatted_request_warning = QtWidgets.QLabel("")
            self.formatted_request_warning.setWordWrap(True)
            self.formatted_request_warning.setStyleSheet(
                "background:#8b5a00;color:white;font-weight:bold;padding:6px;")
            self.formatted_request = QtWidgets.QPlainTextEdit()
            self.formatted_request.setAccessibleName("Formatted Fireworks Request")
            self.formatted_request.setAccessibleDescription(
                "Editable Fireworks addActivity JSON. Valid edits save automatically.")
            formatted_controls = QtWidgets.QHBoxLayout()
            self.save_formatted_request_button = QtWidgets.QPushButton(
                "Save Formatted Request")
            self.regenerate_formatted_request_button = QtWidgets.QPushButton(
                "Regenerate from Structured Results")
            self.connect_fireworks_button = QtWidgets.QPushButton("Connect to Fireworks")
            self.connect_fireworks_button.setAccessibleName("Connect to Fireworks")
            self.submit_fireworks_button = QtWidgets.QPushButton("Submit to Fireworks")
            self.submit_fireworks_button.setAccessibleName("Submit to Fireworks")
            self.fireworks_connection_status = QtWidgets.QLabel("Not connected")
            formatted_controls.addWidget(self.save_formatted_request_button)
            formatted_controls.addWidget(self.regenerate_formatted_request_button)
            formatted_controls.addWidget(self.connect_fireworks_button)
            formatted_controls.addWidget(self.submit_fireworks_button)
            formatted_controls.addWidget(self.fireworks_connection_status)
            formatted_controls.addStretch(1)
            formatted_layout.addLayout(fireworks_fields)
            formatted_layout.addWidget(self.formatted_request_warning)
            exact_payload_label = QtWidgets.QLabel(
                "Exact JSON payload that will be sent by Submit to Fireworks")
            exact_payload_label.setAccessibleName("Exact Fireworks JSON payload")
            formatted_layout.addWidget(exact_payload_label)
            formatted_layout.addLayout(formatted_controls)
            formatted_layout.addWidget(self.formatted_request, 1)
            tabs.addTab(formatted_widget, "Formatted Request")
            self.setting_formatted_request = False
            self.setting_fireworks_controls = False
            self.fireworks_control_values = (None, None)
            self.formatted_request_dirty = False
            self.formatted_request_timer = QtCore.QTimer(self)
            self.formatted_request_timer.setSingleShot(True)
            self.formatted_request_timer.setInterval(750)
            self.formatted_request_timer.timeout.connect(self.save_formatted_request)
            self.formatted_request.textChanged.connect(self.formatted_request_edited)
            self.save_formatted_request_button.clicked.connect(
                lambda: self.save_formatted_request(show_confirmation=True))
            self.regenerate_formatted_request_button.clicked.connect(
                self.regenerate_formatted_request)
            self.fireworks_category.currentIndexChanged.connect(
                self.fireworks_selector_changed)
            self.fireworks_location.currentIndexChanged.connect(
                self.fireworks_selector_changed)
            self.connect_fireworks_button.clicked.connect(self.connect_to_fireworks)
            self.submit_fireworks_button.clicked.connect(self.submit_to_fireworks)
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
            table = QtWidgets.QTableWidget(0, 4)
            table.setHorizontalHeaderLabels(["Name", "Unit IDs (comma-separated)",
                                              "Aliases (comma-separated)",
                                              "Fireworks Staff ID"])
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
            import_fireworks_button = QtWidgets.QPushButton("Import Fireworks Staff…")
            add_button = QtWidgets.QPushButton("Add Row")
            remove_button = QtWidgets.QPushButton("Remove Selected")
            save_button = QtWidgets.QPushButton("Save Roster")
            close_button = QtWidgets.QPushButton("Close")
            for button in (import_button, import_fireworks_button, add_button,
                           remove_button, save_button, close_button):
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
                for column in range(4):
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
                        for column in range(4)))
                try:
                    destination = save_roster_table(roster_path, Path.cwd(), rows)
                    path_label.setText(f"Current roster: {destination}")
                    self.status.setText(f"Saved roster with {len(rows)} table rows")
                    QtWidgets.QMessageBox.information(
                        dialog, "Roster saved", "The updated roster will be used by the next OCR run.")
                except (OSError, ValueError) as exc:
                    QtWidgets.QMessageBox.critical(dialog, "Unable to save roster", str(exc))

            import_button.clicked.connect(import_roster)
            def import_fireworks_staff():
                name, _ = QtWidgets.QFileDialog.getOpenFileName(
                    dialog, "Import Fireworks staff response", "", "JSON files (*.json)")
                if not name:
                    return
                rows = [tuple(
                    table.item(row, column).text() if table.item(row, column) else ""
                    for column in range(4)) for row in range(table.rowCount())]
                try:
                    response = json.loads(unescape(Path(name).read_text(encoding="utf-8")))
                    imported, matched = import_fireworks_staff_ids(rows, response)
                    put_rows(imported)
                    self.status.setText(
                        f"Matched {matched} roster members to Fireworks Staff IDs")
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    QtWidgets.QMessageBox.critical(
                        dialog, "Unable to import Fireworks staff", str(exc))

            import_fireworks_button.clicked.connect(import_fireworks_staff)
            add_button.clicked.connect(add_row)
            remove_button.clicked.connect(remove_rows)
            save_button.clicked.connect(save_roster)
            close_button.clicked.connect(dialog.accept)
            dialog.exec()
            if self.record is not None:
                self.display_record(self.record)

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
                    self.save_formatted_request()
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
            if self.record is not None:
                self.save_formatted_request()
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
                self.setting_formatted_request = True
                self.formatted_request.clear()
                self.setting_formatted_request = False
                self.formatted_request_dirty = False
                self.set_formatted_request_message("")
                self.sync_fireworks_controls({})
                self.clear_record_form()
                self.warning.hide()
                self.status.setText("Queue empty — load a PDF to begin")
                self.update_navigation()
            self.persist_state()

        def remove_all_pdfs(self):
            if self.busy or not self.sources:
                return
            if self.record is not None:
                self.save_formatted_request()
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
            self.setting_formatted_request = True
            self.formatted_request.clear()
            self.setting_formatted_request = False
            self.formatted_request_dirty = False
            self.set_formatted_request_message("")
            self.sync_fireworks_controls({})
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
                self.setting_formatted_request = True
                self.formatted_request.clear()
                self.setting_formatted_request = False
                self.formatted_request_dirty = False
                self.set_formatted_request_message("")
                self.sync_fireworks_controls({})
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
            idle = not self.busy and self.fireworks_future is None
            self.page_label.setText(f"{position} of {count}" +
                                    (f" — {self.source.name}" if self.source else ""))
            self.previous_button.setEnabled(idle and self.current_index > 0)
            self.next_button.setEnabled(idle and 0 <= self.current_index < count - 1)
            self.goto_number.setRange(1, max(1, count))
            if count:
                self.goto_number.setValue(self.current_index + 1)
            self.goto_number.setEnabled(idle and count > 0)
            self.goto_button.setEnabled(idle and count > 0)
            self.remove_button.setEnabled(idle and self.source is not None)
            self.remove_all_button.setEnabled(idle and bool(self.sources))
            self.process_button.setEnabled(idle and self.source is not None)
            self.process_all_button.setEnabled(
                idle and bool(unprocessed_sources(self.sources, self.records)))
            self.stop_button.setEnabled(
                self.busy and self.batch_total > 0 and not self.stop_requested)
            self.add_attendee_button.setEnabled(idle and self.record is not None)
            self.add_attendee_voice_button.setEnabled(idle and self.record is not None)
            self.update_selection_buttons()
            self.update_menu_buttons()
            self.update_fireworks_buttons()

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
                    for name, unit_ids, aliases, _fireworks_id in roster_table_rows(
                            config.roster_path, Path.cwd()):
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
            enabled = (not self.busy and self.fireworks_future is None
                       and self.record is not None
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
            self.accept_stage3_button.setEnabled(
                not self.busy and self.fireworks_future is None
                and suggestion is not None)
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

        def generated_fireworks_request(self, record):
            staff_ids, unresolved_staff = (), tuple(
                str(item.get("print_name") or item.get("unit_id") or "unknown attendee")
                for item in record.get("attendees", ()) if isinstance(item, dict))
            instructor_field = record.get("fields", {}).get("instructor", {})
            instructor = (display_value(instructor_field)
                          if isinstance(instructor_field, dict) else None)
            if instructor not in (None, ""):
                unresolved_staff += (f"Instructor: {instructor}",)
            try:
                if config.roster_path is not None:
                    roster = load_roster(config.roster_path, Path.cwd())
                    staff_ids, unresolved_staff = fireworks_staff_ids(record, roster)
            except (OSError, ValueError):
                pass
            category = (fireworks_mappings.category_named(selected_category_name(record))
                        if fireworks_mappings is not None else None)
            location = (fireworks_mappings.location_named(selected_location_name(record))
                        if fireworks_mappings is not None else None)
            station_id = fireworks_mappings.station_id if fireworks_mappings is not None else 54
            return formatted_fireworks_request(
                record, staff_ids, category=category, location=location,
                station_id=station_id), unresolved_staff

        def set_formatted_request_message(self, message, color="#8b5a00"):
            self.formatted_request_warning.setText(message)
            self.formatted_request_warning.setStyleSheet(
                f"background:{color};color:white;font-weight:bold;padding:6px;")
            self.formatted_request_warning.setVisible(bool(message))

        def sync_fireworks_controls(self, payload):
            category_name = None
            location_name = None
            if fireworks_mappings is not None and isinstance(payload, dict):
                category = fireworks_mappings.category_with_id(payload.get("assignCat"))
                location = fireworks_mappings.location_with_id(payload.get("location"))
                category_name = category.name if category is not None else None
                location_name = location.name if location is not None else None
            self.setting_fireworks_controls = True
            self.fireworks_category.setCurrentIndex(
                max(0, self.fireworks_category.findData(category_name)))
            self.fireworks_location.setCurrentIndex(
                max(0, self.fireworks_location.findData(location_name)))
            self.setting_fireworks_controls = False
            self.fireworks_control_values = (category_name, location_name)

        def restore_fireworks_controls(self):
            category_name, location_name = self.fireworks_control_values
            self.setting_fireworks_controls = True
            self.fireworks_category.setCurrentIndex(
                max(0, self.fireworks_category.findData(category_name)))
            self.fireworks_location.setCurrentIndex(
                max(0, self.fireworks_location.findData(location_name)))
            self.setting_fireworks_controls = False

        def fireworks_selector_changed(self):
            if (self.setting_fireworks_controls or self.setting_formatted_request
                    or self.record is None or fireworks_mappings is None):
                return
            try:
                payload = json.loads(self.formatted_request.toPlainText())
                if not isinstance(payload, dict):
                    raise ValueError("Formatted Request must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                self.restore_fireworks_controls()
                QtWidgets.QMessageBox.warning(
                    self, "Invalid Formatted Request",
                    "Correct the visible JSON before changing its Fireworks dropdowns.\n\n"
                    + str(exc))
                return

            category_name = self.fireworks_category.currentData()
            location_name = self.fireworks_location.currentData()
            try:
                if category_name is None:
                    payload["assignCat"] = None
                else:
                    update_payload_selection(
                        payload, fireworks_mappings, category_name=category_name)
                if location_name is None:
                    payload["location"] = None
                    payload["locationstr"] = None
                    fields = payload.get("locationFlds")
                    if not isinstance(fields, dict):
                        fields = {}
                        payload["locationFlds"] = fields
                    fields["moneln"] = None
                    fields["desc"] = None
                    fields["upsize_ts"] = None
                else:
                    update_payload_selection(
                        payload, fireworks_mappings, location_name=location_name)
                payload["station"] = fireworks_mappings.station_id
                selection = self.record.setdefault("fireworks_selection", {})
                if not isinstance(selection, dict):
                    selection = {}
                    self.record["fireworks_selection"] = selection
                selection["category"] = category_name or ""
                selection["location"] = location_name or ""
                self.fireworks_control_values = (category_name, location_name)
                self.setting_formatted_request = True
                self.formatted_request.setPlainText(
                    json.dumps(payload, indent=2, ensure_ascii=False))
                self.setting_formatted_request = False
                self.formatted_request_dirty = True
                self.save_formatted_request()
                self.status.setText(
                    "Updated the visible Fireworks JSON from the dropdown selection")
            except (OSError, ValueError) as exc:
                self.restore_fireworks_controls()
                QtWidgets.QMessageBox.critical(
                    self, "Unable to update Fireworks request", str(exc))

        def update_fireworks_buttons(self):
            active = self.fireworks_future is not None
            idle = not self.busy and not active
            submission = (self.record.get("fireworks_submission", {})
                          if isinstance(self.record, dict) else {})
            locked = (isinstance(submission, dict)
                      and submission.get("status") in {"submitted", "unknown"})
            mappings_ready = fireworks_mappings is not None
            self.fireworks_category.setEnabled(
                idle and self.record is not None and mappings_ready and not locked)
            self.fireworks_location.setEnabled(
                idle and self.record is not None and mappings_ready and not locked)
            self.connect_fireworks_button.setEnabled(idle)
            self.submit_fireworks_button.setEnabled(
                idle and self.record is not None and mappings_ready and not locked
                and self.fireworks_client is not None
                and self.fireworks_client.connected)
            self.save_formatted_request_button.setEnabled(
                idle and self.record is not None and not locked)
            self.regenerate_formatted_request_button.setEnabled(
                idle and self.record is not None and not locked)
            self.formatted_request.setReadOnly(
                self.record is None or locked or active)

        def connect_to_fireworks(self):
            if self.fireworks_future is not None:
                return
            token, accepted = QtWidgets.QInputDialog.getText(
                self, "Connect to Fireworks",
                "Paste the current Fireworks bearer token. It is kept only in memory "
                "and discarded when this application closes.",
                QtWidgets.QLineEdit.EchoMode.Password)
            if not accepted:
                return
            try:
                client = FireworksClient(
                    token, base_url=args.fireworks_api_base)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(
                    self, "Unable to connect to Fireworks", str(exc))
                return
            if self.fireworks_client is not None:
                self.fireworks_client.clear_token()
            self.fireworks_client = client
            self.fireworks_operation = "connect"
            self.fireworks_pending = None
            self.fireworks_connection_status.setText("Validating…")
            self.fireworks_future = self.executor.submit(client.validate_connection)
            self.fireworks_timer.start()
            self.update_navigation()

        def visible_fireworks_payload(self):
            try:
                payload = json.loads(self.formatted_request.toPlainText())
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError("Formatted Request must be a JSON object")
            return payload

        def submit_to_fireworks(self):
            if (self.record is None or fireworks_mappings is None
                    or self.fireworks_client is None
                    or not self.fireworks_client.connected
                    or self.fireworks_future is not None):
                return
            if not self.save_formatted_request(show_confirmation=False):
                return
            try:
                payload = self.visible_fireworks_payload()
                _generated, unresolved = self.generated_fireworks_request(self.record)
                errors = validate_fireworks_payload(
                    payload, fireworks_mappings, unresolved)
                if errors:
                    raise ValueError("\n".join(f"• {error}" for error in errors))
                submission = self.record.get("fireworks_submission", {})
                if (isinstance(submission, dict)
                        and submission.get("status") in {"submitted", "unknown"}):
                    raise DuplicateSubmissionError(
                        f"This record is already marked {submission.get('status')}")
                fireworks_ledger.assert_may_submit(self.record, payload)
            except (OSError, ValueError, DuplicateSubmissionError) as exc:
                QtWidgets.QMessageBox.warning(
                    self, "Fireworks request is not ready", str(exc))
                return

            category = fireworks_mappings.category_with_id(payload.get("assignCat"))
            location = fireworks_mappings.location_with_id(payload.get("location"))
            answer = QtWidgets.QMessageBox.question(
                self, "Submit one Fireworks activity",
                "Fireworks will receive exactly the JSON currently visible in the "
                "Formatted Request tab as one POST. It will not be retried "
                "automatically if the outcome is uncertain.\n\n"
                f"Title: {payload.get('assignTitle')}\n"
                f"Start: {payload.get('startDt')}\n"
                f"End: {payload.get('endDt')}\n"
                f"Category: {category.name if category else payload.get('assignCat')}\n"
                f"Location: {location.name if location else payload.get('location')}\n"
                f"Station: {fireworks_mappings.station_name} "
                f"({fireworks_mappings.station_id})\n"
                f"Participants: {len(payload.get('staff', []))}\n\n"
                "Submit this reviewed record now?")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return

            self.fireworks_operation = "submit"
            self.fireworks_pending = {
                "record": self.record,
                "source": self.source,
                "payload": payload,
            }
            self.fireworks_connection_status.setText("Submitting one request…")
            self.fireworks_future = self.executor.submit(
                self.fireworks_client.post_activity, payload)
            self.fireworks_timer.start()
            self.update_navigation()

        def persist_fireworks_outcome(
                self, status, payload, *, response=None, error=None):
            entry = fireworks_ledger.append(
                record=self.record, payload=payload, status=status,
                response=response, error=error)
            self.record["fireworks_submission"] = {
                "status": status,
                "recorded_at": entry["recorded_at"],
                "payload_sha256": entry["payload_sha256"],
                "activity_id": entry["activity_id"],
                "error": error,
            }
            automatic_export(self.record, args.export_dir)
            self.persist_state()
            return entry

        def poll_fireworks_result(self):
            if self.fireworks_future is None or not self.fireworks_future.done():
                return
            self.fireworks_timer.stop()
            future, operation = self.fireworks_future, self.fireworks_operation
            pending = self.fireworks_pending
            self.fireworks_future = None
            self.fireworks_operation = None
            self.fireworks_pending = None
            try:
                result = future.result()
                if operation == "connect":
                    self.fireworks_connection_status.setText("Connected — token in memory")
                    self.status.setText("Fireworks connection validated with one read-only lookup")
                elif operation == "submit" and pending is not None:
                    self.record = pending["record"]
                    entry = self.persist_fireworks_outcome(
                        "submitted", pending["payload"], response=result.payload)
                    self.fireworks_connection_status.setText("Connected — activity submitted")
                    self.display_record(self.record)
                    activity = entry.get("activity_id")
                    QtWidgets.QMessageBox.information(
                        self, "Fireworks activity submitted",
                        "Fireworks accepted the activity."
                        + (f"\n\nActivity ID: {activity}" if activity else
                           "\n\nNo activity ID was present in the response; the full receipt was recorded."))
            except FireworksSubmissionUnknown as exc:
                if pending is not None:
                    self.record = pending["record"]
                    try:
                        self.persist_fireworks_outcome(
                            "unknown", pending["payload"], error=str(exc))
                    except OSError:
                        self.record["fireworks_submission"] = {
                            "status": "unknown", "error": str(exc)}
                    self.display_record(self.record)
                QtWidgets.QMessageBox.critical(
                    self, "Fireworks outcome unknown",
                    f"{exc}\n\nDo not submit this record again until Fireworks has been checked.")
            except FireworksSubmissionRejected as exc:
                if pending is not None:
                    self.record = pending["record"]
                    try:
                        self.persist_fireworks_outcome(
                            "rejected", pending["payload"], error=str(exc))
                    except OSError:
                        pass
                    self.display_record(self.record)
                QtWidgets.QMessageBox.warning(
                    self, "Fireworks rejected the activity", str(exc))
            except FireworksConnectionError as exc:
                if self.fireworks_client is not None:
                    self.fireworks_client.clear_token()
                self.fireworks_client = None
                self.fireworks_connection_status.setText("Not connected")
                QtWidgets.QMessageBox.warning(
                    self, "Unable to connect to Fireworks", str(exc))
            except (OSError, ValueError) as exc:
                if operation == "submit" and pending is not None:
                    self.record = pending["record"]
                    self.record["fireworks_submission"] = {
                        "status": "unknown",
                        "error": (
                            "Fireworks responded, but the local receipt could not be "
                            "recorded: " + str(exc)),
                    }
                    try:
                        automatic_export(self.record, args.export_dir)
                        self.persist_state()
                    except OSError:
                        pass
                    self.display_record(self.record)
                QtWidgets.QMessageBox.critical(
                    self, "Unable to record Fireworks result",
                    f"{exc}\n\nDo not resubmit until Fireworks and the local ledger have been checked.")
            finally:
                self.update_navigation()

        def formatted_request_edited(self):
            if not self.setting_formatted_request and self.record is not None:
                self.formatted_request_dirty = True
                self.formatted_request_timer.start()

        def save_formatted_request(self, show_confirmation=False):
            if self.setting_formatted_request or self.record is None:
                return True
            if not self.formatted_request_dirty and not show_confirmation:
                return True
            self.formatted_request_timer.stop()
            valid, error = save_fireworks_request_edit(
                self.record, self.formatted_request.toPlainText(),
                datetime.now(timezone.utc).isoformat())
            try:
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.raw.setPlainText(json.dumps(
                    self.record, indent=2, ensure_ascii=False))
            except OSError as exc:
                self.set_formatted_request_message(
                    f"Unable to save Formatted Request: {exc}", "#8b1e1e")
                return False
            self.formatted_request_dirty = False
            if valid:
                try:
                    self.sync_fireworks_controls(self.visible_fireworks_payload())
                except ValueError:
                    pass
                _payload, unresolved = self.generated_fireworks_request(self.record)
                message = "Manual Formatted Request saved"
                if unresolved:
                    message += "; verify manually unresolved roster attendee(s): " + ", ".join(unresolved)
                self.set_formatted_request_message(message, "#286428")
                if show_confirmation:
                    self.status.setText("Formatted Request saved; automatic export updated")
            else:
                self.set_formatted_request_message(
                    f"UNSAVED AS REQUEST — draft preserved: {error}", "#8b1e1e")
                if show_confirmation:
                    QtWidgets.QMessageBox.warning(
                        self, "Invalid Formatted Request",
                        f"The draft was preserved, but it is not valid request JSON.\n\n{error}")
            return valid

        def regenerate_formatted_request(self):
            if self.record is None:
                return
            answer = QtWidgets.QMessageBox.question(
                self, "Regenerate Formatted Request",
                "Discard this record's manual Formatted Request edits and rebuild it "
                "from Structured Results?")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self.formatted_request_timer.stop()
            self.formatted_request_dirty = False
            prior_review = self.record.get("fireworks_request_review")
            prior_draft = self.record.get("fireworks_request_draft")
            self.record.pop("fireworks_request_review", None)
            self.record.pop("fireworks_request_draft", None)
            try:
                automatic_export(self.record, args.export_dir)
                self.persist_state()
                self.display_record(self.record)
                self.status.setText(
                    "Formatted Request regenerated from Structured Results")
            except OSError as exc:
                if prior_review is not None:
                    self.record["fireworks_request_review"] = prior_review
                if prior_draft is not None:
                    self.record["fireworks_request_draft"] = prior_draft
                QtWidgets.QMessageBox.critical(
                    self, "Unable to regenerate Formatted Request", str(exc))

        def display_record(self, record):
            self.raw.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
            generated, unresolved_staff = self.generated_fireworks_request(record)
            request_text, request_source = displayed_fireworks_request(record, generated)
            self.setting_formatted_request = True
            self.formatted_request.setPlainText(request_text)
            self.setting_formatted_request = False
            self.formatted_request_dirty = False
            try:
                self.sync_fireworks_controls(json.loads(request_text))
            except (json.JSONDecodeError, ValueError):
                self.restore_fireworks_controls()
            submission = record.get("fireworks_submission", {})
            submission_status = (submission.get("status")
                                 if isinstance(submission, dict) else None)
            if request_source == "draft":
                self.set_formatted_request_message(
                    "INVALID JSON DRAFT PRESERVED — correct it to save a request",
                    "#8b1e1e")
            elif submission_status == "submitted":
                activity = submission.get("activity_id")
                self.set_formatted_request_message(
                    "SUBMITTED TO FIREWORKS"
                    + (f" — activity {activity}" if activity else ""), "#286428")
            elif submission_status == "unknown":
                self.set_formatted_request_message(
                    "FIREWORKS OUTCOME UNKNOWN — verify before any resubmission",
                    "#8b1e1e")
            elif request_source == "reviewed":
                message = "Manual Formatted Request saved"
                if unresolved_staff:
                    message += "; verify manually unresolved roster attendee(s): " + ", ".join(unresolved_staff)
                self.set_formatted_request_message(message, "#286428")
            elif unresolved_staff:
                self.set_formatted_request_message(
                    "Fireworks Staff ID unresolved for: " + ", ".join(unresolved_staff))
            else:
                self.set_formatted_request_message("")
            self.build_record_form(structured_rows(record))
            needs_review = record.get("status") == "review_required"
            self.warning.setText("REVIEW REQUIRED — " + ("; ".join(record.get("warnings", ())) or "one or more fields require review"))
            self.warning.setVisible(needs_review); self.status.setText("Complete — review required" if needs_review else "Complete")
            self.update_selection_buttons()
            self.update_fireworks_buttons()

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
            if self.fireworks_future is not None and not self.fireworks_future.done():
                event.ignore(); QtWidgets.QMessageBox.information(
                    self, "Fireworks request in progress",
                    "Wait for the current Fireworks operation to finish before closing."); return
            if self.record is not None:
                try:
                    self.save_formatted_request()
                    automatic_export(self.record, args.export_dir)
                except OSError as exc:
                    event.ignore()
                    QtWidgets.QMessageBox.critical(self, "Unable to save current record", str(exc))
                    return
            self.poll_timer.stop()
            self.fireworks_timer.stop()
            self.persist_state()
            if self.fireworks_client is not None:
                self.fireworks_client.clear_token()
                self.fireworks_client = None
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.preview_temp.cleanup(); super().closeEvent(event)

    app = QtWidgets.QApplication(sys.argv[:1])
    window = Window(); window.show()
    if backup_warning:
        QtWidgets.QMessageBox.warning(
            window, "Startup backup failed",
            "The application opened, but its startup backup could not be created:\n\n"
            + backup_warning)
    if fireworks_mapping_warning:
        QtWidgets.QMessageBox.warning(
            window, "Fireworks mappings unavailable",
            "OCR review remains available, but Fireworks submission is disabled until "
            f"the external mapping file is corrected:\n\n{args.fireworks_ids}\n\n"
            + fireworks_mapping_warning)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
