import unittest

from ki_council.classify import (
    UNCLASSIFIED,
    build_classify_prompt,
    classify_prompts,
    parse_classification,
    segment_labels,
)
from ki_council.promptsets import PromptItem


class ParseClassificationTests(unittest.TestCase):
    def test_plain_label(self):
        self.assertEqual(parse_classification("coding/hard"), ("coding", "hard"))

    def test_label_on_the_last_line_with_chatter_above(self):
        output = "Let me think about this.\nThe prompt asks for code.\nanalysis/medium"
        self.assertEqual(parse_classification(output), ("analysis", "medium"))

    def test_whitespace_and_case_are_tolerated(self):
        self.assertEqual(parse_classification("  Writing / Easy  "), ("writing", "easy"))

    def test_unknown_labels_do_not_become_a_guess(self):
        # A misfiled prompt lands in the wrong segment and quietly skews it.
        # Refusing to classify is the safe failure.
        for output in ("architecture/trivial", "", "I don't know", "coding", None):
            with self.subTest(output=output):
                self.assertEqual(parse_classification(output), (UNCLASSIFIED, UNCLASSIFIED))


class ClassifyPromptsTests(unittest.TestCase):
    def setUp(self):
        self.prompts = [
            PromptItem(id="p1", text="Fix this null pointer", source="t"),
            PromptItem(id="p2", text="Rewrite this email", source="t"),
        ]

    def test_prompts_are_labelled(self):
        labels = {"Fix this null pointer": "coding/hard", "Rewrite this email": "writing/easy"}

        def fake(prompt_text):
            return next(v for k, v in labels.items() if k in prompt_text)

        result = classify_prompts(self.prompts, fake)
        self.assertEqual(result["p1"], ("coding", "hard"))
        self.assertEqual(result["p2"], ("writing", "easy"))

    def test_a_failing_classifier_does_not_kill_the_run(self):
        def boom(prompt_text):
            raise RuntimeError("classifier down")

        result = classify_prompts(self.prompts, boom)
        self.assertEqual(result["p1"], (UNCLASSIFIED, UNCLASSIFIED))

    def test_segment_labels_flattens_by_category_or_difficulty(self):
        classifications = {"p1": ("coding", "hard"), "p2": ("writing", "easy")}
        self.assertEqual(
            segment_labels(classifications, by="category"),
            {"p1": "coding", "p2": "writing"},
        )
        self.assertEqual(
            segment_labels(classifications, by="difficulty"),
            {"p1": "hard", "p2": "easy"},
        )

    def test_prompt_text_reaches_the_classifier(self):
        self.assertIn("Fix this null pointer", build_classify_prompt("Fix this null pointer"))


if __name__ == "__main__":
    unittest.main()
