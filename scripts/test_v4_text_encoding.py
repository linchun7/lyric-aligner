import tempfile
import unittest
from pathlib import Path

from lyric_aligner.io.text import TextEncodingError, read_task_text


class V4TextEncodingTests(unittest.TestCase):
    def test_utf8_is_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ko.txt"
            path.write_text("사랑해", encoding="utf-8")
            self.assertEqual(read_task_text(path), "사랑해")

    def test_cp949_does_not_get_silently_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ko.txt"
            path.write_bytes("사랑해".encode("cp949"))
            with self.assertRaisesRegex(TextEncodingError, "not valid UTF-8"):
                read_task_text(path)
            self.assertEqual(read_task_text(path, encoding="cp949"), "사랑해")


if __name__ == "__main__":
    unittest.main()
