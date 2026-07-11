import os
import tempfile
import unittest
from pathlib import Path

from app.interfaces.errors.exceptions import BadRequestException
from app.services.file import FileService


class DownloadBoundaryTest(unittest.TestCase):
    def test_accepts_regular_file_inside_allowed_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "report.md"
            artifact.write_text("report", encoding="utf-8")

            resolved = FileService.resolve_downloadable_file(
                str(artifact),
                allowed_root=root,
                max_size_bytes=1024,
            )

            self.assertEqual(resolved, artifact.resolve())

    def test_rejects_outside_symlink_special_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "home"
            root.mkdir()
            outside = Path(directory) / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            symlink = root / "leak.txt"
            symlink.symlink_to(outside)
            fifo = root / "stream"
            os.mkfifo(fifo)
            oversized = root / "large.bin"
            oversized.write_bytes(b"x" * 11)

            for candidate in (outside, symlink, fifo, oversized):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(BadRequestException):
                        FileService.resolve_downloadable_file(
                            str(candidate),
                            allowed_root=root,
                            max_size_bytes=10,
                        )


if __name__ == "__main__":
    unittest.main()
