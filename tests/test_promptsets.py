import json
import tempfile
import unittest
from pathlib import Path

from ki_council.promptsets import PromptSetError, load_prompts


class PromptSetTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class JsonlTests(PromptSetTestCase):
    def test_loads_objects_strings_and_skips_junk(self):
        path = self.tmp / "prompts.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"prompt": "Explain quantum entanglement simply."}),
                    json.dumps({"text": "Write a haiku about deadlines."}),
                    json.dumps("Summarize the plot of Faust part one."),
                    "not json at all {{{",
                    json.dumps({"unrelated": "key"}),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        items = load_prompts(str(path))
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].text, "Explain quantum entanglement simply.")

    def test_custom_id_is_used(self):
        path = self.tmp / "prompts.jsonl"
        path.write_text(json.dumps({"id": "q-1", "prompt": "A question that is long enough."}))
        self.assertEqual(load_prompts(str(path))[0].id, "q-1")


class DirectoryAndLinesTests(PromptSetTestCase):
    def test_directory_one_prompt_per_file(self):
        (self.tmp / "a.txt").write_text("First prompt with enough length.")
        (self.tmp / "b.md").write_text("Second prompt, also long enough.")
        (self.tmp / "ignored.py").write_text("print('not a prompt')")
        items = load_prompts(str(self.tmp))
        self.assertEqual([item.id for item in items], ["a", "b"])

    def test_txt_file_one_prompt_per_line(self):
        path = self.tmp / "quick.txt"
        path.write_text("First prompt with enough length.\n\nSecond prompt, also long enough.\n")
        items = load_prompts(str(path))
        self.assertEqual(len(items), 2)


class FilterTests(PromptSetTestCase):
    def test_short_prompts_and_duplicates_dropped_and_limit_applied(self):
        path = self.tmp / "prompts.jsonl"
        lines = [
            json.dumps({"prompt": "hi"}),  # too short
            json.dumps({"prompt": "A sufficiently long prompt."}),
            json.dumps({"prompt": "  a sufficiently   long prompt. "}),  # duplicate
            json.dumps({"prompt": "Another sufficiently long prompt."}),
            json.dumps({"prompt": "Third distinct long prompt here."}),
        ]
        path.write_text("\n".join(lines))
        items = load_prompts(str(path), limit=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].text, "A sufficiently long prompt.")

    def test_empty_source_raises(self):
        path = self.tmp / "prompts.jsonl"
        path.write_text(json.dumps({"prompt": "hi"}))
        with self.assertRaises(PromptSetError):
            load_prompts(str(path))

    def test_missing_path_raises(self):
        with self.assertRaises(PromptSetError):
            load_prompts(str(self.tmp / "nope.jsonl"))


class ChatGPTExportTests(PromptSetTestCase):
    def _write_export(self, conversations):
        path = self.tmp / "conversations.json"
        path.write_text(json.dumps(conversations), encoding="utf-8")
        return path

    def test_extracts_first_user_message_by_create_time(self):
        conversation = {
            "title": "Trip planning",
            "mapping": {
                "n1": {"message": {"author": {"role": "system"}, "create_time": 1,
                                   "content": {"content_type": "text", "parts": ["sys"]}}},
                "n3": {"message": {"author": {"role": "user"}, "create_time": 30,
                                   "content": {"content_type": "text", "parts": ["Follow-up question, ignore me."]}}},
                "n2": {"message": {"author": {"role": "user"}, "create_time": 20,
                                   "content": {"content_type": "text", "parts": ["Plan a three day trip to Rome."]}}},
                "n4": {"message": None},
            },
        }
        items = load_prompts(str(self._write_export([conversation])))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].text, "Plan a three day trip to Rome.")
        self.assertEqual(items[0].id, "Trip planning")

    def test_multimodal_parts_are_ignored(self):
        conversation = {
            "title": "With image",
            "mapping": {
                "n1": {"message": {"author": {"role": "user"}, "create_time": 1,
                                   "content": {"content_type": "multimodal_text",
                                               "parts": [{"asset_pointer": "file://x"}, "Describe this image please."]}}},
            },
        }
        items = load_prompts(str(self._write_export([conversation])))
        self.assertEqual(items[0].text, "Describe this image please.")

    def test_unusable_export_raises_with_hint(self):
        path = self._write_export([{"neither": "format"}])
        with self.assertRaises(PromptSetError):
            load_prompts(str(path))


class ClaudeExportTests(PromptSetTestCase):
    def test_flat_text_field(self):
        conversations = [{
            "name": "Recipe help",
            "chat_messages": [
                {"sender": "assistant", "text": "Hello!"},
                {"sender": "human", "text": "Give me a recipe for shakshuka."},
            ],
        }]
        path = self.tmp / "conversations.json"
        path.write_text(json.dumps(conversations), encoding="utf-8")
        items = load_prompts(str(path))
        self.assertEqual(items[0].text, "Give me a recipe for shakshuka.")
        self.assertEqual(items[0].id, "Recipe help")

    def test_block_style_content(self):
        conversations = [{
            "name": "Blocks",
            "chat_messages": [
                {"sender": "human", "text": "",
                 "content": [{"type": "text", "text": "Explain the CAP theorem briefly."}]},
            ],
        }]
        path = self.tmp / "conversations.json"
        path.write_text(json.dumps(conversations), encoding="utf-8")
        items = load_prompts(str(path))
        self.assertEqual(items[0].text, "Explain the CAP theorem briefly.")


if __name__ == "__main__":
    unittest.main()
