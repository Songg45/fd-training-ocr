import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from fd_training_ocr.table_extraction import (CellScore, RowScore, contiguous_populated_rows,
                                              detect_populated_rows, suppress_printed_rules)
from fd_training_ocr.template import Region, TemplateDefinition


def definition() -> TemplateDefinition:
    regions = []
    for row, y in enumerate((0.1, 0.3, 0.5), 1):
        regions.extend((
            Region(f"attendee.{row:02}.unit_id", "attendee_cell", (0.1, y, 0.2, 0.15), {"row": row}),
            Region(f"attendee.{row:02}.print_name", "attendee_cell", (0.3, y, 0.4, 0.15), {"row": row}),
            Region(f"attendee.{row:02}.signature", "signature", (0.7, y, 0.2, 0.15), {"row": row}),
        ))
    return TemplateDefinition("synthetic", "v1", "normalized_xywh", (500, 300),
                              frozenset({"signature"}), {}, tuple(regions))


def pages(rows: set[int], signature_only: set[int] = set()) -> tuple[Image.Image, Image.Image]:
    master = Image.new("L", (500, 300), 255)
    completed = master.copy()
    draw = ImageDraw.Draw(completed)
    for region in definition().regions:
        row = int(region.metadata["row"])
        if (region.kind == "attendee_cell" and row in rows and region.name.endswith("print_name")) or \
                (region.kind == "signature" and row in signature_only):
            box = region.pixel_box(*master.size)
            draw.line((box[0] + 5, box[1] + 8, box[2] - 5, box[3] - 7), fill=0, width=4)
    return master, completed


class TableExtractionTests(unittest.TestCase):
    def test_signature_ink_never_populates_a_row(self) -> None:
        master, completed = pages({2}, signature_only={1, 3})
        populated = {score.row for score in detect_populated_rows(master, completed, definition()) if score.populated}
        self.assertEqual(populated, {2})

    def test_synthetic_row_precision_and_recall_are_perfect(self) -> None:
        tp = fp = fn = 0
        for expected in (set(), {1}, {2, 3}, {1, 2, 3}):
            master, completed = pages(expected, signature_only={1, 3} - expected)
            predicted = {score.row for score in detect_populated_rows(master, completed, definition()) if score.populated}
            tp += len(expected & predicted); fp += len(predicted - expected); fn += len(expected - predicted)
        self.assertEqual(tp / (tp + fp), 1.0)
        self.assertEqual(tp / (tp + fn), 1.0)

    def test_rule_suppression_removes_rule_but_keeps_crossing_handwriting(self) -> None:
        master = Image.new("L", (120, 50), 255)
        ImageDraw.Draw(master).line((0, 35, 119, 35), fill=0, width=2)
        completed = master.copy()
        ImageDraw.Draw(completed).line((20, 8, 90, 42), fill=0, width=5)
        result = np.asarray(suppress_printed_rules(master, completed)) < 128
        self.assertLess(result[35].sum(), 20)
        self.assertGreater(result.sum(), 100)

    def test_localized_difference_can_populate_row_when_net_ink_is_low(self) -> None:
        master, completed = pages({1})
        score = detect_populated_rows(master, completed, definition(),
                                      threshold=1.0, difference_threshold=.01)[0]
        self.assertTrue(score.populated)

    def test_grid_difference_with_negative_added_ink_does_not_populate_rows(self) -> None:
        master = Image.new("L", (500, 300), 160)
        completed = Image.new("L", (500, 300), 255)
        with patch("fd_training_ocr.table_extraction.difference_mask",
                   side_effect=lambda a, b: np.ones((a.height, a.width), dtype=bool)):
            scores = detect_populated_rows(master, completed, definition())
        self.assertFalse(any(score.populated for score in scores))

    def test_isolated_lower_row_false_positives_are_not_selected(self) -> None:
        def row(number: int, populated: bool) -> RowScore:
            unit = CellScore(f"attendee.{number:02d}.unit_id", 0.1, 0.01)
            name = CellScore(f"attendee.{number:02d}.print_name", 0.1, 0.01)
            return RowScore(number, unit, name, populated)
        scores = tuple(row(number, number in {1, 2, 16, 18}) for number in range(1, 20))
        self.assertEqual(contiguous_populated_rows(scores), (1, 2))

    def test_no_lower_rows_selected_when_first_row_is_blank(self) -> None:
        def row(number: int, populated: bool) -> RowScore:
            cell = CellScore(f"attendee.{number:02d}.unit_id", 0.1, 0.01)
            return RowScore(number, cell, cell, populated)
        scores = tuple(row(number, number in {16, 18}) for number in range(1, 20))
        self.assertEqual(contiguous_populated_rows(scores), ())

    def test_strong_two_cell_first_row_is_recovered_when_second_row_is_populated(self) -> None:
        def row(number: int, populated: bool, unit_difference: float,
                name_difference: float) -> RowScore:
            unit = CellScore(f"attendee.{number:02d}.unit_id", unit_difference, -.01)
            name = CellScore(f"attendee.{number:02d}.print_name", name_difference, -.001)
            return RowScore(number, unit, name, populated)
        scores = (row(1, False, .12092, .11688), row(2, True, .13859, .15557),
                  row(3, False, .07, .06))
        self.assertEqual(contiguous_populated_rows(scores), (1, 2))


if __name__ == "__main__":
    unittest.main()
