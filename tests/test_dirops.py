"""
tests/test_dirops.py - Comprehensive unit tests for Kushagra's dirops.py module.

Tests:
  - Directory creation and '.' / '..' link initialization
  - xv6 nlink conventions (directory nlink is 1 + subdirs; '.' is not counted)
  - Nested directory creation and parent nlink increments
  - Regular file creation and nlink verification
  - Unlinking files and reclaiming inodes and data blocks
  - Unlinking empty directories and decrementing parent nlink
  - Preventing unlinking of non-empty directories (ENOTEMPTY)
  - Preventing unlinking of '.' and '..' (EINVAL)
  - Slot reuse in dirlink (reusing entries with inum == 0)
  - Filename length boundary checks (<= 14 characters)
  - Duplicate creation prevention (EEXIST)
"""

import os
import tempfile
import unittest
import errno

from xv6defs import (
    ROOTINO, T_DIR, T_FILE, T_UNUSED, DIRSIZ, DIRENT_SIZE
)
from xv6img import XV6Image, create_empty_image
import alloc
import dirops


class TestDirops(unittest.TestCase):

    def setUp(self):
        # Create a temporary xv6 image file for each test
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_path = self.temp_file.name
        self.temp_file.close()

        # Initialize standard clean image with 1000 blocks and 100 inodes
        create_empty_image(self.temp_path, nblocks=1000, ninodes=100)
        self.img = XV6Image(self.temp_path)

    def tearDown(self):
        self.img.close()
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_initial_root_directory(self):
        """Verify root directory has inum 1, type T_DIR, and initial nlink == 1."""
        root = self.img.read_dinode(ROOTINO)
        self.assertEqual(root.type, T_DIR)
        # xv6 convention: root directory nlink is 1 (1 + 0 subdirectories)
        self.assertEqual(root.nlink, 1)

        entries = {name: inum for inum, name in self.img.get_dirents(ROOTINO)}
        self.assertIn(".", entries)
        self.assertIn("..", entries)
        self.assertEqual(entries["."], ROOTINO)
        self.assertEqual(entries[".."], ROOTINO)

    def test_mkdir_and_nlink_conventions(self):
        """
        Verify mkdir creates '.' and '..', sets child nlink to 1,
        and increments parent nlink by 1 (for child's '..').
        """
        child_inum = dirops.mkdir(self.img, "/subdir")
        self.assertGreater(child_inum, 1)

        # Check child inode
        child = self.img.read_dinode(child_inum)
        self.assertEqual(child.type, T_DIR)
        # CRITICAL XV6 RULE: child.nlink == 1 because '.' is NOT counted!
        self.assertEqual(child.nlink, 1, "Child directory nlink must be 1 (xv6 does not count '.')")

        # Check parent (root) inode
        root = self.img.read_dinode(ROOTINO)
        # Parent nlink is incremented by 1 (for child's '..')
        self.assertEqual(root.nlink, 2, "Parent directory nlink must be incremented by 1")

        # Check child directory contents
        child_entries = {name: inum for inum, name in self.img.get_dirents(child_inum)}
        self.assertEqual(child_entries["."], child_inum)
        self.assertEqual(child_entries[".."], ROOTINO)

    def test_nested_subdirectories(self):
        """Verify parent link counts with multiple nested subdirectories."""
        d1 = dirops.mkdir(self.img, "/d1")
        d2 = dirops.mkdir(self.img, "/d1/d2")
        d3 = dirops.mkdir(self.img, "/d1/d3")

        # Root has 1 subdirectory (d1), so root.nlink == 2
        self.assertEqual(self.img.read_dinode(ROOTINO).nlink, 2)

        # d1 has 2 subdirectories (d2, d3), so d1.nlink == 1 + 2 = 3
        self.assertEqual(self.img.read_dinode(d1).nlink, 3)

        # Leaf directories have 0 subdirectories, so nlink == 1
        self.assertEqual(self.img.read_dinode(d2).nlink, 1)
        self.assertEqual(self.img.read_dinode(d3).nlink, 1)

    def test_create_regular_file(self):
        """Verify creating regular files does NOT increment parent directory nlink."""
        file_inum = dirops.create(self.img, "/hello.txt", T_FILE)
        f_inode = self.img.read_dinode(file_inum)
        self.assertEqual(f_inode.type, T_FILE)
        self.assertEqual(f_inode.nlink, 1)

        # Root directory nlink should remain 1 (regular files do not have '..')
        root = self.img.read_dinode(ROOTINO)
        self.assertEqual(root.nlink, 1)

        # Write and read data to confirm inode works
        test_data = b"Hello from xv6 FUSE!"
        alloc.writei(self.img, file_inum, test_data, 0)
        read_back = self.img.readi(file_inum, 0, len(test_data))
        self.assertEqual(read_back, test_data)

    def test_unlink_regular_file(self):
        """Verify unlinking a regular file removes the entry and frees the inode."""
        file_inum = dirops.create(self.img, "/remove_me.txt", T_FILE)
        alloc.writei(self.img, file_inum, b"Data to be freed", 0)

        # Verify exists
        self.assertIsNotNone(self.img.namei("/remove_me.txt"))

        # Unlink file
        ret = dirops.unlink(self.img, "/remove_me.txt")
        self.assertEqual(ret, 0)

        # Verify no longer in namespace
        self.assertIsNone(self.img.namei("/remove_me.txt"))

        # Verify inode is freed (type == T_UNUSED)
        inode = self.img.read_dinode(file_inum)
        self.assertEqual(inode.type, T_UNUSED)

    def test_unlink_empty_directory(self):
        """Verify unlinking an empty directory decrements parent nlink and reclaims inode."""
        dir_inum = dirops.mkdir(self.img, "/emptydir")
        self.assertEqual(self.img.read_dinode(ROOTINO).nlink, 2)

        ret = dirops.unlink(self.img, "/emptydir")
        self.assertEqual(ret, 0)

        # Verify parent nlink decremented back to 1
        self.assertEqual(self.img.read_dinode(ROOTINO).nlink, 1)

        # Verify directory inode is freed
        self.assertEqual(self.img.read_dinode(dir_inum).type, T_UNUSED)
        self.assertIsNone(self.img.namei("/emptydir"))

    def test_unlink_non_empty_directory_fails(self):
        """Verify unlinking a directory containing files raises ENOTEMPTY."""
        dirops.mkdir(self.img, "/dir_with_file")
        dirops.create(self.img, "/dir_with_file/inner.txt", T_FILE)

        with self.assertRaises(OSError) as ctx:
            dirops.unlink(self.img, "/dir_with_file")
        self.assertEqual(ctx.exception.errno, errno.ENOTEMPTY)

        # Directory and parent nlink should be intact
        self.assertEqual(self.img.read_dinode(ROOTINO).nlink, 2)
        self.assertIsNotNone(self.img.namei("/dir_with_file/inner.txt"))

    def test_unlink_dot_and_dotdot_fails(self):
        """Verify unlinking '.' or '..' is prohibited."""
        dirops.mkdir(self.img, "/mydir")

        with self.assertRaises(OSError) as ctx:
            dirops.unlink(self.img, "/mydir/.")
        self.assertEqual(ctx.exception.errno, errno.EINVAL)

        with self.assertRaises(OSError) as ctx:
            dirops.unlink(self.img, "/mydir/..")
        self.assertEqual(ctx.exception.errno, errno.EINVAL)

    def test_dirlink_slot_reuse(self):
        """
        Verify that dirlink reuses empty slots (de.inum == 0) left by unlinked files
        instead of constantly growing the directory size.
        """
        dirops.mkdir(self.img, "/slot_test")
        dp_inum = self.img.namei("/slot_test")

        # Create 3 files
        dirops.create(self.img, "/slot_test/file1", T_FILE)
        dirops.create(self.img, "/slot_test/file2", T_FILE)
        dirops.create(self.img, "/slot_test/file3", T_FILE)

        # Directory size before unlink: 5 entries (., .., file1, file2, file3) = 80 bytes
        dp_before = self.img.read_dinode(dp_inum)
        size_before = dp_before.size
        self.assertEqual(size_before, 5 * DIRENT_SIZE)

        # Unlink file2 (the 4th entry at offset 48)
        dirops.unlink(self.img, "/slot_test/file2")

        # Create a new file4
        dirops.create(self.img, "/slot_test/file4", T_FILE)

        # Verify directory size did NOT increase because file4 reused file2's slot
        dp_after = self.img.read_dinode(dp_inum)
        self.assertEqual(dp_after.size, size_before, "dirlink should reuse deleted entry slot")

        # Verify file4 is accessible
        self.assertIsNotNone(self.img.namei("/slot_test/file4"))

    def test_filename_length_limits(self):
        """Verify DIRSIZ (14 chars) boundary: 14 chars succeeds, 15 chars raises ENAMETOOLONG."""
        valid_name = "a" * 14
        too_long_name = "b" * 15

        # 14 chars should succeed
        inum = dirops.create(self.img, f"/{valid_name}", T_FILE)
        self.assertGreater(inum, 1)
        self.assertIsNotNone(self.img.namei(f"/{valid_name}"))

        # 15 chars should fail
        with self.assertRaises(OSError) as ctx:
            dirops.create(self.img, f"/{too_long_name}", T_FILE)
        self.assertEqual(ctx.exception.errno, errno.ENAMETOOLONG)

    def test_duplicate_creation_fails(self):
        """Verify creating an already existing directory raises EEXIST."""
        dirops.mkdir(self.img, "/unique")
        with self.assertRaises(OSError) as ctx:
            dirops.mkdir(self.img, "/unique")
        self.assertEqual(ctx.exception.errno, errno.EEXIST)

    def test_hard_link_and_multi_unlink(self):
        """Verify hard links increment nlink and unlinking only reclaims when nlink hits 0."""
        f_inum = dirops.create(self.img, "/orig.txt", T_FILE)
        alloc.writei(self.img, f_inum, b"Shared file content", 0)

        # Create hard link
        ret = dirops.link(self.img, "/orig.txt", "/linked.txt")
        self.assertEqual(ret, 0)

        # Inode nlink should now be 2
        inode = self.img.read_dinode(f_inum)
        self.assertEqual(inode.nlink, 2)

        # Unlink the original path
        dirops.unlink(self.img, "/orig.txt")
        self.assertIsNone(self.img.namei("/orig.txt"))

        # Inode should still be active with nlink = 1
        inode = self.img.read_dinode(f_inum)
        self.assertEqual(inode.type, T_FILE)
        self.assertEqual(inode.nlink, 1)

        # Linked file should still read the same content
        data = self.img.readi(f_inum, 0, 19)
        self.assertEqual(data, b"Shared file content")

        # Unlink the second link -> now inode is freed
        dirops.unlink(self.img, "/linked.txt")
        self.assertIsNone(self.img.namei("/linked.txt"))
        inode_after = self.img.read_dinode(f_inum)
        self.assertEqual(inode_after.type, T_UNUSED)



if __name__ == '__main__':
    unittest.main()
