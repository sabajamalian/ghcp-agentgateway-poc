import os
from pathlib import Path
import tempfile
import unittest

from retention import expire


class RetentionTests(unittest.TestCase):
    def test_only_old_matching_regular_files_are_deleted(self):
        now = 2000000000
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("metadata-2026-01-01.jsonl", "metadata-2026-01-02.jsonl", "ca.key"):
                (root / name).write_text("fixture")
            os.utime(root / "metadata-2026-01-01.jsonl", (now - 8 * 86400,) * 2)
            os.utime(root / "ca.key", (now - 8 * 86400,) * 2)
            os.utime(root / "metadata-2026-01-02.jsonl", (now,) * 2)
            (root / "metadata-2026-01-03.jsonl").symlink_to(root / "ca.key")
            expire(root, r"metadata-\d{4}-\d{2}-\d{2}\.jsonl", now)
            self.assertFalse((root / "metadata-2026-01-01.jsonl").exists())
            self.assertTrue((root / "metadata-2026-01-02.jsonl").exists())
            self.assertTrue((root / "ca.key").exists())
            self.assertTrue((root / "metadata-2026-01-03.jsonl").is_symlink())


if __name__ == "__main__":
    unittest.main()
