"""
fsck.py - Filesystem consistency checker for xv6 disk images.

Validates:
  1. Superblock integrity (magic number, boundaries).
  2. Inode validity (valid type, valid block addresses).
  3. Direct & indirect block pointers (within data block range, no duplicates).
  4. Free block bitmap consistency (every referenced block is marked in bitmap;
     no unreferenced data block is marked in bitmap).
  5. Directory structure ('.' points to self, '..' points to parent, valid dirents).
  6. Inode link count consistency according to xv6 rules:
     - Regular files: nlink == number of directory references.
     - Directories: nlink == 1 + number of subdirectories ('.' is not counted).
"""

import sys
import struct
from collections import defaultdict
from typing import Dict, Set, List, Tuple

from xv6defs import (
    BSIZE, BPB, FSMAGIC, ROOTINO, T_UNUSED, T_DIR, T_FILE, T_DEVICE,
    NDIRECT, NINDIRECT, MAXFILE, DIRENT_SIZE, Dinode, Dirent, BBLOCK
)
from xv6img import XV6Image


class ConsistencyError(Exception):
    pass


class XV6Fsck:
    def __init__(self, img_path: str):
        self.img = XV6Image(img_path)
        self.sb = self.img.sb
        self.errors: List[str] = []

    def log_error(self, msg: str):
        self.errors.append(msg)

    def close(self):
        if hasattr(self, 'img') and self.img:
            self.img.close()

    def run(self) -> bool:
        try:
            print(f"Checking filesystem image: {self.img.filepath}")
            self.check_superblock()
            if self.errors:
                self._print_results()
                return False

            datastart = self.sb.bmapstart + (self.sb.size + BPB - 1) // BPB
            used_blocks: Dict[int, List[str]] = defaultdict(list)
            inode_ref_counts: Dict[int, int] = defaultdict(int)
            dir_subdir_counts: Dict[int, int] = defaultdict(int)
            visited_dirs: Set[int] = set()

            self.check_inodes_and_blocks(datastart, used_blocks)
            self.check_directories(ROOTINO, ROOTINO, visited_dirs, inode_ref_counts, dir_subdir_counts)
            self.check_link_counts(inode_ref_counts, dir_subdir_counts)
            self.check_bitmap(datastart, set(used_blocks.keys()))

            return self._print_results()
        finally:
            self.close()

    def check_superblock(self):
        sb = self.sb
        if sb.magic != FSMAGIC:
            self.log_error(f"Invalid magic number: 0x{sb.magic:x} (expected 0x{FSMAGIC:x})")
        if sb.size <= 0:
            self.log_error(f"Invalid filesystem size: {sb.size}")
        if sb.ninodes <= 0:
            self.log_error(f"Invalid number of inodes: {sb.ninodes}")

    def check_inodes_and_blocks(self, datastart: int, used_blocks: Dict[int, List[str]]):
        sb = self.sb
        for inum in range(1, sb.ninodes):
            inode = self.img.read_dinode(inum)
            if inode.type == T_UNUSED:
                continue

            if inode.type not in (T_DIR, T_FILE, T_DEVICE):
                self.log_error(f"Inode {inum} has invalid type: {inode.type}")
                continue

            # Check direct blocks
            for i in range(NDIRECT):
                b = inode.addrs[i]
                if b != 0:
                    if b < datastart or b >= sb.size:
                        self.log_error(f"Inode {inum} direct block {i} out of range: {b}")
                    else:
                        used_blocks[b].append(f"inode {inum} direct block {i}")

            # Check indirect blocks
            indir_b = inode.addrs[NDIRECT]
            if indir_b != 0:
                if indir_b < datastart or indir_b >= sb.size:
                    self.log_error(f"Inode {inum} indirect block out of range: {indir_b}")
                else:
                    used_blocks[indir_b].append(f"inode {inum} indirect pointer block")
                    indir_data = self.img.read_block(indir_b)
                    for j in range(NINDIRECT):
                        b = struct.unpack_from("<I", indir_data, j * 4)[0]
                        if b != 0:
                            if b < datastart or b >= sb.size:
                                self.log_error(f"Inode {inum} indirect block entry {j} out of range: {b}")
                            else:
                                used_blocks[b].append(f"inode {inum} indirect entry {j}")

        # Check for duplicate block allocations
        for b, refs in used_blocks.items():
            if len(refs) > 1:
                self.log_error(f"Block {b} is referenced multiple times: {', '.join(refs)}")

    def check_directories(self, inum: int, expected_parent: int, visited_dirs: Set[int],
                          inode_ref_counts: Dict[int, int], dir_subdir_counts: Dict[int, int]):
        if inum in visited_dirs:
            self.log_error(f"Directory loop detected at inode {inum}")
            return
        visited_dirs.add(inum)

        inode = self.img.read_dinode(inum)
        if inode.type != T_DIR:
            self.log_error(f"Expected inode {inum} to be a directory")
            return

        has_dot = False
        has_dotdot = False

        off = 0
        while off < inode.size:
            data = self.img.readi(inum, off, DIRENT_SIZE)
            if len(data) < DIRENT_SIZE:
                break
            de = Dirent.unpack(data)
            if de.inum != 0:
                if de.name == ".":
                    has_dot = True
                    if de.inum != inum:
                        self.log_error(f"Directory {inum} '.' points to {de.inum} instead of {inum}")
                elif de.name == "..":
                    has_dotdot = True
                    if de.inum != expected_parent:
                        self.log_error(f"Directory {inum} '..' points to {de.inum} instead of {expected_parent}")
                else:
                    child_inode = self.img.read_dinode(de.inum)
                    if child_inode.type == T_UNUSED:
                        self.log_error(f"Directory {inum} entry '{de.name}' references unused inode {de.inum}")
                    elif child_inode.type == T_DIR:
                        dir_subdir_counts[inum] += 1
                        self.check_directories(de.inum, inum, visited_dirs, inode_ref_counts, dir_subdir_counts)
                    else:
                        inode_ref_counts[de.inum] += 1
            off += DIRENT_SIZE

        if not has_dot:
            self.log_error(f"Directory {inum} missing '.' entry")
        if not has_dotdot:
            self.log_error(f"Directory {inum} missing '..' entry")

    def check_link_counts(self, inode_ref_counts: Dict[int, int], dir_subdir_counts: Dict[int, int]):
        sb = self.sb
        for inum in range(1, sb.ninodes):
            inode = self.img.read_dinode(inum)
            if inode.type == T_UNUSED:
                continue

            if inode.type == T_DIR:
                # xv6 directory rule: nlink == 1 + number of subdirectories
                expected_nlink = 1 + dir_subdir_counts[inum]
                if inode.nlink != expected_nlink:
                    self.log_error(
                        f"Directory inode {inum} nlink mismatch: on-disk={inode.nlink}, expected={expected_nlink} "
                        f"(1 + {dir_subdir_counts[inum]} subdirectories)"
                    )
            elif inode.type == T_FILE:
                expected_nlink = inode_ref_counts[inum]
                if inode.nlink != expected_nlink:
                    self.log_error(
                        f"File inode {inum} nlink mismatch: on-disk={inode.nlink}, expected={expected_nlink} references"
                    )

    def check_bitmap(self, datastart: int, referenced_data_blocks: Set[int]):
        sb = self.sb
        # Build set of all blocks marked as used in bitmap
        marked_used_blocks: Set[int] = set()
        for b in range(0, sb.size, BPB):
            bblock = BBLOCK(b, sb)
            bitmap_data = self.img.read_block(bblock)
            limit = min(BPB, sb.size - b)
            for bi in range(limit):
                byte_idx = bi // 8
                bit_idx = bi % 8
                if (bitmap_data[byte_idx] & (1 << bit_idx)) != 0:
                    marked_used_blocks.add(b + bi)

        # Check metadata blocks (0 through datastart - 1)
        for b in range(datastart):
            if b not in marked_used_blocks:
                self.log_error(f"Metadata block {b} is marked free in bitmap")

        # Check data blocks
        for b in range(datastart, sb.size):
            is_referenced = (b in referenced_data_blocks)
            is_marked = (b in marked_used_blocks)
            if is_referenced and not is_marked:
                self.log_error(f"Data block {b} is used by an inode but marked FREE in bitmap")
            elif not is_referenced and is_marked:
                self.log_error(f"Data block {b} is marked USED in bitmap but unreferenced (leak)")

    def _print_results(self) -> bool:
        if not self.errors:
            print("fsck passed: No errors found. Filesystem is clean and consistent.")
            return True
        else:
            print(f"fsck failed with {len(self.errors)} error(s):")
            for err in self.errors:
                print(f"  - {err}")
            return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 fsck.py <fs.img>")
        sys.exit(1)

    checker = XV6Fsck(sys.argv[1])
    success = checker.run()
    sys.exit(0 if success else 1)
