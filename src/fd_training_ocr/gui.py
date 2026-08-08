"""Local PySide6 desktop front end for one-form OCR and review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from .config import load_config
from .gui_controller import (GuiPaths, build_processor, export_record, process_pdf,
                             structured_rows, validate_pdf)
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

    class Worker(QtCore.QObject):
        # Use a serialized payload across the thread boundary.  Passing a Python
        # object through an AutoConnection allowed PySide to invoke the receiver
        # in the worker thread on Windows, which caused QTextDocument/QTimer
        # affinity failures when the result widgets were populated.
        completed = QtCore.Signal(str)
        failed = QtCore.Signal(str)

        def __init__(self, source):
            super().__init__()
            self.source = source

        @QtCore.Slot()
        def run(self):
            try:
                record = process_pdf(self.source, build_processor(config, paths))
                self.completed.emit(json.dumps(record, ensure_ascii=False))
            except Exception as exc:
                self.failed.emit(f"{type(exc).__name__}: {exc}")

    class Window(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.source = None
            self.record = None
            self.preview_temp = tempfile.TemporaryDirectory(prefix="fd-training-ocr-gui-")
            self.thread = None
            self.setWindowTitle("FD Training OCR")
            self.resize(1350, 850)
            central = QtWidgets.QWidget(); self.setCentralWidget(central)
            layout = QtWidgets.QVBoxLayout(central)
            controls = QtWidgets.QHBoxLayout(); layout.addLayout(controls)
            self.load_button = QtWidgets.QPushButton("Load PDF")
            self.process_button = QtWidgets.QPushButton("Process")
            self.export_button = QtWidgets.QPushButton("Export Results")
            self.process_button.setEnabled(False); self.export_button.setEnabled(False)
            self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0)
            self.status = QtWidgets.QLabel("Load a PDF to begin")
            for widget in (self.load_button, self.process_button, self.export_button,
                           self.progress, self.status): controls.addWidget(widget)
            self.warning = QtWidgets.QLabel("")
            self.warning.setStyleSheet("background:#8b1e1e;color:white;font-weight:bold;padding:8px;")
            self.warning.hide(); layout.addWidget(self.warning)
            splitter = QtWidgets.QSplitter(); layout.addWidget(splitter, 1)
            self.preview = Preview(); splitter.addWidget(self.preview)
            tabs = QtWidgets.QTabWidget(); splitter.addWidget(tabs); splitter.setSizes([700, 650])
            self.table = QtWidgets.QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["Field", "Result", "Warnings"])
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            tabs.addTab(self.table, "Structured Results")
            self.raw = QtWidgets.QPlainTextEdit(); self.raw.setReadOnly(True)
            tabs.addTab(self.raw, "Raw JSON")
            self.load_button.clicked.connect(self.load_pdf)
            self.process_button.clicked.connect(self.process)
            self.export_button.clicked.connect(self.export)

        def load_pdf(self):
            name, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load training form", "", "PDF files (*.pdf)")
            if not name: return
            try:
                self.source = validate_pdf(Path(name))
                pages = render_pdf(self.source, Path(self.preview_temp.name), dpi=300,
                                   pdftoppm=paths.pdftoppm)
                self.preview.show_image(pages[0].path)
                self.record = None; self.raw.clear(); self.table.setRowCount(0)
                self.process_button.setEnabled(True); self.export_button.setEnabled(False)
                self.warning.hide(); self.status.setText(self.source.name)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Unable to load PDF", str(exc))

        def process(self):
            self.set_busy(True); self.status.setText("Processing locally: Stages 1–2 Qwen2.5-VL, Stage 3 Qwen3-VL…")
            self.thread = QtCore.QThread(self); worker = Worker(self.source); worker.moveToThread(self.thread)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            self.thread.started.connect(worker.run, queued)
            worker.completed.connect(self.finished, queued)
            worker.failed.connect(self.failed, queued)
            worker.completed.connect(self.thread.quit, queued)
            worker.failed.connect(self.thread.quit, queued)
            self.thread.finished.connect(worker.deleteLater)
            self.thread.finished.connect(self.thread.deleteLater); self.thread.worker = worker
            self.thread.start()

        def set_busy(self, busy):
            self.load_button.setEnabled(not busy); self.process_button.setEnabled(not busy and self.source is not None)
            self.export_button.setEnabled(not busy and self.record is not None)
            self.progress.setRange(0, 0 if busy else 1)
            if not busy: self.progress.setValue(1)

        @QtCore.Slot(str)
        def finished(self, payload):
            record = json.loads(payload)
            self.record = record; self.raw.setPlainText(json.dumps(record, indent=2, ensure_ascii=False))
            rows = structured_rows(record); self.table.setRowCount(len(rows))
            for row, values in enumerate(rows):
                for column, value in enumerate(values): self.table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
            needs_review = record.get("status") == "review_required"
            self.warning.setText("REVIEW REQUIRED — " + ("; ".join(record.get("warnings", ())) or "one or more fields require review"))
            self.warning.setVisible(needs_review); self.status.setText("Complete — review required" if needs_review else "Complete")
            self.set_busy(False)

        @QtCore.Slot(str)
        def failed(self, message):
            self.set_busy(False); self.status.setText("Processing failed")
            QtWidgets.QMessageBox.critical(self, "OCR failed", message)

        def export(self):
            if self.record is None: return
            suggested = str(paths.output_dir / f"{self.record['source_sha256']}.json")
            name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export results", suggested, "JSON files (*.json)")
            if name:
                try:
                    export_record(self.record, Path(name)); self.status.setText(f"Exported {Path(name).name}")
                except OSError as exc: QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))

        def closeEvent(self, event):
            if self.thread is not None and self.thread.isRunning():
                event.ignore(); QtWidgets.QMessageBox.information(self, "Processing", "Wait for local OCR to finish before closing."); return
            self.preview_temp.cleanup(); super().closeEvent(event)

    app = QtWidgets.QApplication(sys.argv[:1])
    window = Window(); window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
