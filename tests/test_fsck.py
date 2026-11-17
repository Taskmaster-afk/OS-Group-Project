"""
tests/test_fsck.py - Tests for fsck consistency checker.
"""

import os
import tempfile
import unittest

from xv6defs import T_FILE
from xv6img import XV6Image, create_empty_image
from fsck import XV6Fsck
import alloc
import dirops


class TestFsck(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_path = self.temp_file.name
        self.temp_file.close()
        create_empty_image(self.temp_path, nblocks=1000, ninodes=100)

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_clean_image_passes(self):
        checker = XV6Fsck(self.temp_path)
        self.assertTrue(checker.run())

    def test_after_files_and_directories_passes(self):
        img = XV6Image(self.temp_path)
        d1 = dirops.mkdir(img, "/docs")
        f1 = dirops.create(img, "/docs/readme.txt", T_FILE)
        alloc.writei(img, f1, b"Consistency check test data\n", 0)
        img.close()

        checker = XV6Fsck(self.temp_path)
        self.assertTrue(checker.run())


if __name__ == "__main__":
    unittest.main()

