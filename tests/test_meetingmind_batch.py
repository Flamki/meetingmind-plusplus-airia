import tempfile
import unittest
from pathlib import Path

from meetingmind_batch import gather_files, hash_text


class MeetingMindBatchTests(unittest.TestCase):
    def test_hash_text_stable(self) -> None:
        self.assertEqual(hash_text("abc"), hash_text("abc"))
        self.assertNotEqual(hash_text("abc"), hash_text("abcd"))

    def test_gather_files_recursive_and_non_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("one", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "b.txt").write_text("two", encoding="utf-8")

            top = gather_files(root, "*.txt", recursive=False)
            deep = gather_files(root, "*.txt", recursive=True)

            self.assertEqual(len(top), 1)
            self.assertEqual(len(deep), 2)


if __name__ == "__main__":
    unittest.main()
