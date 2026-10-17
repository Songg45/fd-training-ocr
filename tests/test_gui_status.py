"""Exercise the real Status dialog without OCR, credentials, or live API calls."""

import ast
import hashlib
import gc
from datetime import datetime
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fd_training_ocr import gui
from fd_training_ocr.config import AppConfig
from fd_training_ocr.fireworks_client import (DuplicateSubmissionError, FireworksClient,
                                               LOCKED_SUBMISSION_STATUSES,
                                               SubmissionCoordinator, SubmissionLedger)
from fd_training_ocr.gui_controller import (load_gui_state, load_human_reviews,
                                             automatic_export,
                                             process_status_rows, queue_index_for_page,
                                             save_queue_session, unprocessed_sources)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    QtCore = QtWidgets = None


@unittest.skipIf(QtWidgets is None, "optional PySide6 GUI dependencies not installed")
class StatusDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        # Window currently lives inside main(); compile its unmodified Status method
        # with a small host so tests cannot start OCR or access real deployment data.
        tree = ast.parse(Path(gui.__file__).read_text(encoding="utf-8"))
        main = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        window = next(node for node in main.body
                      if isinstance(node, ast.ClassDef) and node.name == "Window")
        methods = [node for node in window.body if isinstance(node, ast.FunctionDef)
                   and node.name in {"show_process_status", "mark_already_submitted"}]
        namespace = dict(QtCore=QtCore, QtWidgets=QtWidgets, datetime=datetime,
                         process_status_rows=process_status_rows,
                         queue_index_for_page=queue_index_for_page,
                         LOCKED_SUBMISSION_STATUSES=LOCKED_SUBMISSION_STATUSES,
                         DuplicateSubmissionError=DuplicateSubmissionError,
                         automatic_export=automatic_export)
        exec(compile(ast.Module(body=methods, type_ignores=[]), gui.__file__, "exec"), namespace)

        class Host(QtWidgets.QMainWindow):
            show_process_status = namespace["show_process_status"]
            mark_already_submitted = namespace["mark_already_submitted"]

            def __init__(self, root):
                super().__init__()
                self.root = root
                self.sources = [root / f"form-{number}.pdf" for number in range(1, 4)]
                for source in self.sources:
                    source.write_bytes(f"%PDF test {source.name}".encode())
                self.current_index = 0
                self.records = {self.sources[0]: {
                    "source_file": self.sources[0].name,
                    "source_sha256": hashlib.sha256(self.sources[0].read_bytes()).hexdigest(),
                    "page": 1,
                    "fields": {"date": {"normalized": "12/17/25"}},
                    "fireworks_submission": {"status": "submitted", "activity_id": 94}}}
                self.source = self.sources[0]
                self.record = self.records[self.source]
                self.busy = False
                self.fireworks_future = None
                self.status = QtWidgets.QLabel(self)
                self.ledger = SubmissionLedger(root / "submissions.jsonl")
                namespace["fireworks_submissions"] = SubmissionCoordinator(self.ledger)
                namespace["args"] = SimpleNamespace(export_dir=root / "exports")
                self.human_reviews = {}
                self.navigated = None
                self.save_failure = False

            def persist_state(self, **_kwargs):
                if self.save_failure:
                    raise OSError("test save failure")
                save_queue_session(self.root / "state.json", self.sources, self.current_index,
                                   self.records, {}, saved_queue=self.root / "queue.json",
                                   human_reviews=self.human_reviews)
                return True

            def navigate_to(self, index):
                self.navigated = index

            def display_record(self, record):
                self.record = record

            def save_formatted_request(self):
                return True

            def prepare_second_record(self):
                self.source = self.sources[1]
                self.current_index = 1
                self.record = {
                    "source_file": self.source.name,
                    "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    "page": 1, "fields": {"date": {"normalized": "08/04/26"}},
                    "fireworks_request_draft": {"text": "invalid unfinished JSON"},
                }
                self.records[self.source] = self.record

        cls.Host = Host

    def tearDown(self):
        # Drain QObject ownership while QApplication is still alive. Parent-owned
        # modal dialogs otherwise outlive Python test locals during process shutdown.
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def use_dialog(self, host, action):
        failures = []

        def interact():
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsNotNone(dialog)
                action(dialog, dialog.findChild(QtWidgets.QTableWidget))
            except BaseException as exc:
                failures.append(exc)
            finally:
                dialog.reject()

        QtCore.QTimer.singleShot(0, interact)
        host.show_process_status()
        if failures:
            raise failures[0]

    @staticmethod
    def review_cell(table, number):
        row = next(row for row in range(table.rowCount())
                   if table.item(row, 1).text() == str(number))
        return row, table.cellWidget(row, 3)

    def test_sorted_dropdowns_save_correct_pdf_and_survive_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            host = self.Host(Path(directory).resolve())
            original_record = str(host.records)

            def edit(dialog, table):
                self.assertEqual(table.columnCount(), 4)
                self.assertEqual(table.horizontalHeaderItem(3).text(), "Human Review")
                self.assertIs(self.review_cell(table, 1)[1].currentData(), True)
                self.assertEqual(self.review_cell(table, 2)[1].currentIndex(), -1)
                self.assertEqual(self.review_cell(table, 3)[1].currentIndex(), -1)
                for column in range(4):
                    for order in (QtCore.Qt.SortOrder.AscendingOrder,
                                  QtCore.Qt.SortOrder.DescendingOrder):
                        table.sortItems(column, order)
                        for number in range(1, 4):
                            _, combo = self.review_cell(table, number)
                            self.assertEqual(combo.accessibleName(), f"Human Review for PDF {number}")
                table.sortItems(3, QtCore.Qt.SortOrder.AscendingOrder)
                self.review_cell(table, 2)[1].setCurrentIndex(0)
                self.review_cell(table, 1)[1].setCurrentIndex(1)
                self.review_cell(table, 3)[1].setCurrentIndex(1)
                self.assertEqual(host.human_reviews,
                                 dict(zip(host.sources, (False, True, False))))
                self.assertEqual(str(host.records), original_record)
                self.assertEqual(unprocessed_sources(host.sources, host.records), tuple(host.sources[1:]))
                for path in (host.root / "state.json", host.root / "queue.json"):
                    self.assertEqual(load_human_reviews(path, host.sources), host.human_reviews)

            self.use_dialog(host, edit)
            sources, index, records, _ = load_gui_state(host.root / "state.json")
            host.sources, host.current_index, host.records = sources, index, records
            host.human_reviews = load_human_reviews(host.root / "state.json", sources)

            def reopen_and_go(dialog, table):
                for number, expected in enumerate((False, True, False), start=1):
                    self.assertIs(self.review_cell(table, number)[1].currentData(), expected)
                table.sortItems(3, QtCore.Qt.SortOrder.DescendingOrder)
                row, _ = self.review_cell(table, 2)
                table.selectRow(row)
                button = next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                              if button.text() == "Go To PDF")
                button.click()

            self.use_dialog(host, reopen_and_go)
            self.assertEqual(host.navigated, 1)
            host.close()

    def test_failed_save_restores_blank_default_and_explicit_false(self):
        with tempfile.TemporaryDirectory() as directory:
            host = self.Host(Path(directory).resolve())
            host.human_reviews[host.sources[0]] = False
            host.save_failure = True

            def edit(_dialog, table):
                self.review_cell(table, 2)[1].setCurrentIndex(0)
                self.assertEqual(self.review_cell(table, 2)[1].currentIndex(), -1)
                self.review_cell(table, 1)[1].setCurrentIndex(0)
                self.assertIs(self.review_cell(table, 1)[1].currentData(), False)
                self.assertEqual(host.human_reviews, {host.sources[0]: False})

            with patch.object(QtWidgets.QMessageBox, "critical") as error:
                self.use_dialog(host, edit)
                self.assertEqual(error.call_count, 2)
            host.close()

    def schedule_mark_decision(self, activity_id="", *, cancel=False):
        failures = []

        def decide():
            dialog = self.app.activeModalWidget()
            try:
                self.assertEqual(dialog.windowTitle(), "Mark Already Submitted to Fireworks")
                buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
                accept = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
                self.assertFalse(accept.isEnabled())
                if cancel:
                    dialog.reject()
                    return
                dialog.findChild(QtWidgets.QLineEdit).setText(activity_id)
                dialog.findChild(QtWidgets.QCheckBox).setChecked(True)
                self.assertTrue(accept.isEnabled())
                accept.click()
            except BaseException as exc:
                failures.append(exc)
                dialog.reject()

        QtCore.QTimer.singleShot(0, decide)
        return failures

    def test_mark_already_submitted_from_sorted_status_writes_receipt_without_post(self):
        with tempfile.TemporaryDirectory() as directory:
            host = self.Host(Path(directory).resolve())
            host.prepare_second_record()
            host.human_reviews[host.source] = False

            def mark(dialog, table):
                table.sortItems(2, QtCore.Qt.SortOrder.DescendingOrder)
                row, _ = self.review_cell(table, 2)
                table.selectRow(row)
                mark_button = next(button for button in dialog.findChildren(QtWidgets.QPushButton)
                                   if button.text() == "Mark Already Submitted…")
                self.assertTrue(mark_button.isEnabled())
                failures = self.schedule_mark_decision("9876")
                mark_button.click()
                if failures:
                    raise failures[0]
                row, combo = self.review_cell(table, 2)
                self.assertEqual(table.item(row, 2).text(), "True")
                self.assertIs(combo.currentData(), False)
                self.assertFalse(mark_button.isEnabled())
                self.assertEqual(gui.record_banner_state(host.record),
                                 ("Submitted to Fireworks — Activity ID: 9876", "#286428"))
                self.assertIn("2 submitted", dialog.findChild(QtWidgets.QLabel).text())

            with patch.object(FireworksClient, "post_activity") as post:
                self.use_dialog(host, mark)
                post.assert_not_called()
            receipt = host.ledger.entries()[0]
            self.assertIsNone(receipt["payload"])
            self.assertIsNone(receipt["response"])
            self.assertEqual(receipt["activity_id"], 9876)
            self.assertEqual(load_gui_state(host.root / "queue.json")[2][host.source]
                             ["fireworks_submission"]["status"], "submitted")
            self.assertTrue(any((host.root / "exports").glob("*.json")))
            host.close()

    def test_cancel_invalid_id_and_blank_id_do_not_require_valid_request_or_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            host = self.Host(Path(directory).resolve())
            host.prepare_second_record()
            failures = self.schedule_mark_decision(cancel=True)
            self.assertFalse(host.mark_already_submitted(host.source))
            self.assertFalse(failures)
            self.assertEqual(host.ledger.entries(), ())
            failures = self.schedule_mark_decision("not an ID")
            with patch.object(QtWidgets.QMessageBox, "critical") as error:
                self.assertFalse(host.mark_already_submitted(host.source))
                error.assert_called_once()
            self.assertFalse(failures)
            self.assertEqual(host.ledger.entries(), ())
            failures = self.schedule_mark_decision()
            self.assertTrue(host.mark_already_submitted(host.source))
            self.assertFalse(failures)
            self.assertIsNone(host.ledger.entries()[0]["activity_id"])
            self.assertEqual(gui.record_banner_state(host.record),
                             ("Submitted to Fireworks", "#286428"))
            host.close()

    def test_save_failure_after_mark_keeps_durable_submitted_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            host = self.Host(Path(directory).resolve())
            host.prepare_second_record()
            host.save_failure = True
            failures = self.schedule_mark_decision()
            with patch.object(QtWidgets.QMessageBox, "critical") as error:
                self.assertFalse(host.mark_already_submitted(host.source))
                error.assert_called_once()
            self.assertFalse(failures)
            self.assertEqual(host.ledger.effective_entry(host.record)["status"], "submitted")
            with self.assertRaises(DuplicateSubmissionError):
                host.ledger.assert_may_submit(host.record, {})
            host.close()

    def test_full_window_button_preserves_pending_draft_and_shows_both_green_banners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            namespace = dict(vars(gui))
            ledger = SubmissionLedger(root / "submissions.jsonl")
            namespace.update(
                QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets,
                args=SimpleNamespace(state_file=root / "state.json", export_dir=root / "exports",
                                     queues_dir=root / "queues"),
                config=AppConfig(), fireworks_mappings=None,
                fireworks_ledger=ledger, fireworks_submissions=SubmissionCoordinator(ledger))
            tree = ast.parse(Path(gui.__file__).read_text(encoding="utf-8"))
            main = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "main")
            classes = [node for node in main.body if isinstance(node, ast.ClassDef)]
            exec(compile(ast.Module(body=classes, type_ignores=[]), gui.__file__, "exec"), namespace)
            window = namespace["Window"]()
            source = root / "form.pdf"
            source.write_bytes(b"%PDF synthetic form")
            window.sources, window.current_index, window.source = [source], 0, source
            window.record = {"source_file": source.name, "source_sha256": "d" * 64,
                             "page": 1, "fields": {"date": {"normalized": "08/04/26"}}}
            window.records[source] = window.record
            window.display_record(window.record)
            self.assertTrue(window.mark_submitted_button.isEnabled())
            window.formatted_request.setPlainText("unfinished pending JSON")
            self.assertTrue(window.formatted_request_dirty)
            failures = self.schedule_mark_decision("94")
            with patch.object(FireworksClient, "post_activity") as post:
                window.mark_submitted_button.click()
                post.assert_not_called()
            self.assertFalse(failures)
            self.assertEqual(window.warning.text(), "Submitted to Fireworks — Activity ID: 94")
            self.assertEqual(window.formatted_request_warning.text(), "SUBMITTED TO FIREWORKS — activity 94")
            self.assertEqual(window.formatted_request.toPlainText(), "unfinished pending JSON")
            self.assertTrue(window.formatted_request.isReadOnly())
            self.assertFalse(window.mark_submitted_button.isEnabled())
            self.assertFalse(window.submit_fireworks_button.isEnabled())
            self.assertEqual(ledger.entries()[0]["activity_id"], 94)
            self.assertIsNone(ledger.entries()[0]["payload"])
            window.close()


if __name__ == "__main__":
    unittest.main()
