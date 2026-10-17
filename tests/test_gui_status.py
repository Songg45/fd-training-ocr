"""Exercise the real Status dialog without OCR, credentials, or live API calls."""

import ast
from datetime import datetime
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fd_training_ocr import gui
from fd_training_ocr.gui_controller import (load_gui_state, load_human_reviews,
                                             process_status_rows, queue_index_for_page,
                                             save_queue_session, unprocessed_sources)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtCore, QtWidgets
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
        method = next(node for node in window.body
                      if isinstance(node, ast.FunctionDef) and node.name == "show_process_status")
        namespace = dict(QtCore=QtCore, QtWidgets=QtWidgets, datetime=datetime,
                         process_status_rows=process_status_rows,
                         queue_index_for_page=queue_index_for_page,
                         args=SimpleNamespace(export_dir=None),
                         fireworks_submissions=SimpleNamespace(
                             reconcile_record=lambda record: (False, None)))
        exec(compile(ast.Module(body=[method], type_ignores=[]), gui.__file__, "exec"), namespace)

        class Host(QtWidgets.QMainWindow):
            show_process_status = namespace["show_process_status"]

            def __init__(self, root):
                super().__init__()
                self.root = root
                self.sources = [root / f"form-{number}.pdf" for number in range(1, 4)]
                for source in self.sources:
                    source.write_bytes(b"%PDF test")
                self.current_index = 0
                self.records = {self.sources[0]: {
                    "fields": {"date": {"normalized": "12/17/25"}},
                    "fireworks_submission": {"status": "submitted", "activity_id": 94}}}
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

        cls.Host = Host

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


if __name__ == "__main__":
    unittest.main()
