import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from fd_training_ocr.checkbox_detection import OptionScore, detect_options
from fd_training_ocr.template import Region, TemplateDefinition


def definition() -> TemplateDefinition:
    return TemplateDefinition("synthetic", "v1", "normalized_xywh", (400, 200),
                              frozenset({"signature"}), {}, (
        Region("training_type.driver", "option", (0.10, 0.10, 0.30, 0.25), {}),
        Region("facility.classroom", "option", (0.10, 0.40, 0.30, 0.25), {}),
        Region("truck.brush54", "option", (0.55, 0.10, 0.30, 0.25), {}),
    ))


def pages(marked: set[str]) -> tuple[Image.Image, Image.Image]:
    master = Image.new("L", (400, 200), 255)
    draw = ImageDraw.Draw(master)
    for region in definition().regions:
        box = region.pixel_box(*master.size)
        draw.line((box[0], box[3] - 5, box[2], box[3] - 5), fill=0, width=2)
    completed = master.copy()
    draw = ImageDraw.Draw(completed)
    for region in definition().regions:
        if region.name in marked:
            box = region.pixel_box(*master.size)
            draw.line((box[0] + 12, box[1] + 5, box[0] + 30, box[3] - 2), fill=0, width=5)
            draw.line((box[0] + 30, box[3] - 2, box[2] - 8, box[1] + 2), fill=0, width=5)
    return master, completed


class CheckboxDetectionTests(unittest.TestCase):
    def test_detects_crossing_marks_without_blank_false_positives(self) -> None:
        master, completed = pages({"training_type.driver", "truck.brush54"})
        scores = detect_options(master, completed, definition())
        selected = {score.name for score in scores if score.selected}
        self.assertEqual(selected, {"training_type.driver", "truck.brush54"})

    def test_synthetic_precision_and_recall_are_perfect(self) -> None:
        tp = fp = fn = 0
        cases = [set(), {"facility.classroom"}, {"training_type.driver", "truck.brush54"},
                 {region.name for region in definition().regions}]
        for expected in cases:
            master, completed = pages(expected)
            predicted = {score.name for score in detect_options(master, completed, definition()) if score.selected}
            tp += len(expected & predicted)
            fp += len(predicted - expected)
            fn += len(expected - predicted)
        self.assertEqual(tp / (tp + fp), 1.0)
        self.assertEqual(tp / (tp + fn), 1.0)

    def test_truck_requires_localized_new_ink_not_only_darkness(self) -> None:
        master, _ = pages(set())
        completed = master.point(lambda value: max(0, value - 20))
        truck = next(score for score in detect_options(master, completed, definition())
                     if score.name == "truck.brush54")
        self.assertFalse(truck.selected)

    def test_clear_faint_group_winner_is_selected(self) -> None:
        master, completed = pages(set())
        region = definition().region("facility.classroom")
        box = region.pixel_box(*master.size)
        draw = ImageDraw.Draw(completed)
        draw.rectangle(box, fill=255)
        draw.rectangle((box[0] + 8, box[1] + 8, box[0] + 47, box[1] + 17), fill=0)
        scores = detect_options(master, completed, definition())
        selected = {score.name for score in scores if score.selected}
        self.assertEqual(selected, {"facility.classroom"})

    def test_recovers_second_faint_facility_mark(self) -> None:
        master, completed = pages(set())
        multi_definition = TemplateDefinition(
            "synthetic", "v1", "normalized_xywh", (400, 200), frozenset(), {}, (
                Region("training_type.driver", "option", (.01, .01, .1, .1), {}),
                Region("facility.classroom", "option", (.12, .01, .1, .1), {}),
                Region("facility.drill_ground", "option", (.23, .01, .1, .1), {}),
                Region("facility.outside_area", "option", (.34, .01, .1, .1), {}),
                Region("truck.brush54", "option", (.45, .01, .1, .1), {}),
            ))
        synthetic = (
            OptionScore("training_type.driver", 0, 1, .01, 0, False),
            OptionScore("facility.classroom", 73, 1000, .07336, .00004, False),
            OptionScore("facility.drill_ground", 65, 1000, .06528, -.00036, False),
            OptionScore("facility.outside_area", 69, 1000, .06936, .00522, True),
            OptionScore("truck.brush54", 0, 1, .01, 0, False),
        )
        with patch("fd_training_ocr.checkbox_detection.score_option",
                   side_effect=synthetic):
            scores = detect_options(master, completed, multi_definition)
        selected = {score.name for score in scores if score.selected}
        self.assertEqual(selected, {"facility.classroom", "facility.outside_area"})


if __name__ == "__main__":
    unittest.main()
