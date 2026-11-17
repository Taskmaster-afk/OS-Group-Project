"""
xv6img.py - Image access layer for xv6 file systems.

Handles raw block I/O, inode read/write, data block reading, directory
lookup, and path resolution. Corresponds to kernel/fs.c and kernel/bio.c.
"""

import os
import struct
import errno
from typing import Optional, Tuple, List
from xv6defs import (
    BSIZE, FSMAGIC, ROOTINO, T_DIR, T_FILE, T_UNUSED,
    DIRSIZ, DIRENT_SIZE, IPB, NDIRECT, MAXFILE, Superblock, Dinode, Dirent,
    IBLOCK, BBLOCK
)
import alloc


class XV6Image:
    """Provides high-level filesystem operations over an xv6 raw disk image."""

    def __init__(self, filepath: str, mode: str = "r+b"):
        self.filepath = filepath
        self.f = open(filepath, mode)
        # Superblock is in block 1
        self.sb = self._read_superblock()

    def close(self):
        if self.f and not self.f.closed:
            self.f.flush()
            self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _read_superblock(self) -> Superblock:
        data = self.read_block(1)
        sb = Superblock.unpack(data[:32])
        if sb.magic != FSMAGIC:
            raise ValueError(f"Invalid superblock magic: 0x{sb.magic:x} (expected 0x{FSMAGIC:x})")
        return sb

    def read_block(self, b: int) -> bytes:
        """Read a single block of BSIZE bytes."""
        self.f.seek(b * BSIZE)
        data = self.f.read(BSIZE)
        if len(data) < BSIZE:
            raise IOError(f"Truncated read at block {b}: expected {BSIZE}, got {len(data)}")
        return data

    def write_block(self, b: int, data: bytes) -> None:
        """Write a single block of BSIZE bytes."""
        if len(data) != BSIZE:
            raise ValueError(f"Block write must be exactly {BSIZE} bytes, got {len(data)}")
        self.f.seek(b * BSIZE)
        self.f.write(data)

    def read_dinode(self, inum: int) -> Dinode:
        """Read struct dinode for inode number `inum`."""
        if inum < 1 or inum >= self.sb.ninodes:
            raise ValueError(f"Inode number out of range: {inum}")
        block_num = IBLOCK(inum, self.sb)
        offset_in_block = (inum % IPB) * 64
        block_data = self.read_block(block_num)
        dinode_data = block_data[offset_in_block:offset_in_block + 64]
        return Dinode.unpack(dinode_data)

    def write_dinode(self, inum: int, dinode: Dinode) -> None:
        """Write struct dinode for inode number `inum`."""
        if inum < 1 or inum >= self.sb.ninodes:
            raise ValueError(f"Inode number out of range: {inum}")
        block_num = IBLOCK(inum, self.sb)
        offset_in_block = (inum % IPB) * 64
        block_data = bytearray(self.read_block(block_num))
        block_data[offset_in_block:offset_in_block + 64] = dinode.pack()
        self.write_block(block_num, bytes(block_data))

    def readi(self, inum: int, offset: int, n: int) -> bytes:
        """
        Read up to `n` bytes from inode `inum` starting at byte `offset`.
        """
        if offset < 0:
            raise ValueError("Negative offset")

        inode = self.read_dinode(inum)
        if offset >= inode.size or n <= 0:
            return b""

        n = min(n, inode.size - offset)
        result = bytearray()
        read_total = 0

        while read_total < n:
            curr_offset = offset + read_total
            bn = curr_offset // BSIZE
            boff = curr_offset % BSIZE
            chunk_len = min(n - read_total, BSIZE - boff)

            pblock = alloc.bmap(self, inode, inum, bn, alloc=False)
            if pblock == 0:
                # Sparse block or unallocated
                result.extend(b'\x00' * chunk_len)
            else:
                data = self.read_block(pblock)
                result.extend(data[boff:boff + chunk_len])

            read_total += chunk_len

        return bytes(result)

    def dirlookup(self, dp_inum: int, name: str) -> Optional[Tuple[int, int]]:
        """
        Look up `name` in directory `dp_inum`.
        Returns (inum, offset_in_dir) if found, or None if not found.
        """
        dp = self.read_dinode(dp_inum)
        if dp.type != T_DIR:
            raise OSError(errno.ENOTDIR, f"Inode {dp_inum} is not a directory")

        off = 0
        while off < dp.size:
            data = self.readi(dp_inum, off, DIRENT_SIZE)
            if len(data) < DIRENT_SIZE:
                break
            de = Dirent.unpack(data)
            if de.inum != 0 and de.name == name:
                return (de.inum, off)
            off += DIRENT_SIZE

        return None

    def get_dirents(self, dp_inum: int) -> List[Tuple[int, str]]:
        """
        Return a list of (inum, name) for all active entries in directory `dp_inum`.
        """
        dp = self.read_dinode(dp_inum)
        if dp.type != T_DIR:
            raise OSError(errno.ENOTDIR, f"Inode {dp_inum} is not a directory")

        entries = []
        off = 0
        while off < dp.size:
            data = self.readi(dp_inum, off, DIRENT_SIZE)
            if len(data) < DIRENT_SIZE:
                break
            de = Dirent.unpack(data)
            if de.inum != 0:
                entries.append((de.inum, de.name))
            off += DIRENT_SIZE

        return entries

    def namei(self, path: str) -> Optional[int]:
        """
        Resolve an absolute or relative path to its inode number.
        Returns the inum, or None if the path does not exist.
        """
        parts = [p for p in path.strip('/').split('/') if p]
        curr_inum = ROOTINO

        for part in parts:
            res = self.dirlookup(curr_inum, part)
            if res is None:
                return None
            curr_inum = res[0]

        return curr_inum

    def nameiparent(self, path: str) -> Tuple[Optional[int], str]:
        """
        Resolve the parent directory and final component name of `path`.
        Returns (parent_inum, basename).
        If path is root ('/'), returns (None, "").
        If parent directory does not exist, returns (None, basename).
        """
        parts = [p for p in path.strip('/').split('/') if p]
        if not parts:
            return (None, "")

        basename = parts[-1]
        parent_parts = parts[:-1]
        curr_inum = ROOTINO

        for part in parent_parts:
            res = self.dirlookup(curr_inum, part)
            if res is None:
                return (None, basename)
            curr_inum = res[0]

        return (curr_inum, basename)


def create_empty_image(filepath: str, nblocks: int = 2000, ninodes: int = 200, nlog: int = 30) -> None:
    """
    Creates a new clean xv6 filesystem image file on disk, mimicking mkfs.c.
    Initializes boot block, superblock, log blocks, inode blocks, bitmap, and root directory.
    """
    from xv6defs import BPB
    
    # Calculate layout
    # Block 0: boot block
    # Block 1: superblock
    # Block 2..2+nlog-1: log blocks
    # Block inodestart..inodestart+ninodeblocks-1: inode blocks
    # Block bmapstart..bmapstart+nbmapblocks-1: bitmap blocks
    # Remaining: data blocks
    logstart = 2
    inodestart = 2 + nlog
    ninodeblocks = (ninodes + IPB - 1) // IPB
    bmapstart = inodestart + ninodeblocks
    nbmapblocks = (nblocks + BPB - 1) // BPB
    datastart = bmapstart + nbmapblocks

    sb = Superblock(
        magic=FSMAGIC,
        size=nblocks,
        nblocks=nblocks - datastart,
        ninodes=ninodes,
        nlog=nlog,
        logstart=logstart,
        inodestart=inodestart,
        bmapstart=bmapstart
    )

    with open(filepath, "wb") as f:
        # Zero out the entire disk image
        zero_block = b'\x00' * BSIZE
        for _ in range(nblocks):
            f.write(zero_block)

        # Write superblock at block 1
        f.seek(1 * BSIZE)
        f.write(sb.pack())

        # Build bitmap: mark metadata blocks (0 through datastart) as allocated (1)
        bitmap_bytes = bytearray(nbmapblocks * BSIZE)
        for b in range(datastart + 1):  # datastart block itself will hold root directory
            byte_idx = b // 8
            bit_idx = b % 8
            bitmap_bytes[byte_idx] |= (1 << bit_idx)

        f.seek(bmapstart * BSIZE)
        f.write(bitmap_bytes)

        # Initialize root directory inode (ROOTINO = 1)
        root_data_block = datastart
        # Root directory in xv6 contains '.' and '..'
        # In xv6, root directory dinode.nlink is 1 (directory's nlink is 1 + subdirs)
        root_inode = Dinode(
            type=T_DIR,
            major=0,
            minor=0,
            nlink=1,
            size=2 * DIRENT_SIZE,
            addrs=[root_data_block] + [0] * NDIRECT
        )

        # Write root inode into inodestart block
        f.seek(IBLOCK(ROOTINO, sb) * BSIZE + (ROOTINO % IPB) * 64)
        f.write(root_inode.pack())

        # Write '.' and '..' in root data block
        dot = Dirent(ROOTINO, ".").pack()
        dotdot = Dirent(ROOTINO, "..").pack()
        root_block_data = dot + dotdot + (b'\x00' * (BSIZE - 2 * DIRENT_SIZE))
        f.seek(root_data_block * BSIZE)
        f.write(root_block_data)
