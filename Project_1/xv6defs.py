"""
xv6defs.py - Constants and on-disk data structures for the xv6 filesystem.

Matches kernel/fs.h and kernel/stat.h from xv6-riscv.
Disk layout:
[ boot block | super block | log | inode blocks | free bit map | data blocks ]
"""

import struct
from typing import NamedTuple, List

# Core filesystem constants
ROOTINO = 1           # Root inode number
BSIZE = 1024          # Block size in bytes
FSMAGIC = 0x10203040  # Superblock magic number

NDIRECT = 12                         # Number of direct blocks
NINDIRECT = BSIZE // 4               # 256 uint32 block addresses in indirect block
MAXFILE = NDIRECT + NINDIRECT        # 268 blocks max file size (274,432 bytes)

DIRSIZ = 14                          # Maximum directory entry file name length

# File types (from kernel/stat.h)
T_UNUSED = 0          # Free / unallocated inode
T_DIR = 1             # Directory
T_FILE = 2            # Regular file
T_DEVICE = 3          # Special device file

# Inode size and layout
DINODE_SIZE = 64      # sizeof(struct dinode) = 64 bytes
IPB = BSIZE // DINODE_SIZE  # 16 inodes per block

# Bitmap layout
BPB = BSIZE * 8       # 8192 bits per bitmap block

# Struct formats (little-endian '<')
# struct superblock:
#   uint magic;
#   uint size;
#   uint nblocks;
#   uint ninodes;
#   uint nlog;
#   uint logstart;
#   uint inodestart;
#   uint bmapstart;
SUPERBLOCK_FORMAT = "<8I"
SUPERBLOCK_SIZE = struct.calcsize(SUPERBLOCK_FORMAT)  # 32 bytes

# struct dinode:
#   short type;
#   short major;
#   short minor;
#   short nlink;
#   uint size;
#   uint addrs[NDIRECT+1];
DINODE_FORMAT = "<hhhhI" + f"{NDIRECT + 1}I"
assert struct.calcsize(DINODE_FORMAT) == DINODE_SIZE, f"DINODE_FORMAT size mismatch: {struct.calcsize(DINODE_FORMAT)}"

# struct dirent:
#   ushort inum;
#   char name[DIRSIZ];
DIRENT_FORMAT = f"<H{DIRSIZ}s"
DIRENT_SIZE = struct.calcsize(DIRENT_FORMAT)  # 16 bytes
assert DIRENT_SIZE == 16, f"DIRENT_SIZE mismatch: {DIRENT_SIZE}"


class Superblock:
    """Represents xv6 superblock metadata."""
    __slots__ = ('magic', 'size', 'nblocks', 'ninodes', 'nlog', 'logstart', 'inodestart', 'bmapstart')

    def __init__(self, magic: int, size: int, nblocks: int, ninodes: int,
                 nlog: int, logstart: int, inodestart: int, bmapstart: int):
        self.magic = magic
        self.size = size
        self.nblocks = nblocks
        self.ninodes = ninodes
        self.nlog = nlog
        self.logstart = logstart
        self.inodestart = inodestart
        self.bmapstart = bmapstart

    @classmethod
    def unpack(cls, data: bytes) -> 'Superblock':
        fields = struct.unpack_from(SUPERBLOCK_FORMAT, data, 0)
        return cls(*fields)

    def pack(self) -> bytes:
        return struct.pack(
            SUPERBLOCK_FORMAT,
            self.magic, self.size, self.nblocks, self.ninodes,
            self.nlog, self.logstart, self.inodestart, self.bmapstart
        )


class Dinode:
    """Represents an xv6 on-disk inode (struct dinode)."""
    __slots__ = ('type', 'major', 'minor', 'nlink', 'size', 'addrs')

    def __init__(self, type: int = T_UNUSED, major: int = 0, minor: int = 0,
                 nlink: int = 0, size: int = 0, addrs: List[int] = None):
        self.type = type
        self.major = major
        self.minor = minor
        self.nlink = nlink
        self.size = size
        self.addrs = list(addrs) if addrs is not None else [0] * (NDIRECT + 1)

    @classmethod
    def unpack(cls, data: bytes) -> 'Dinode':
        fields = struct.unpack(DINODE_FORMAT, data)
        type_, major, minor, nlink, size = fields[:5]
        addrs = list(fields[5:])
        return cls(type_, major, minor, nlink, size, addrs)

    def pack(self) -> bytes:
        return struct.pack(
            DINODE_FORMAT,
            self.type, self.major, self.minor, self.nlink, self.size,
            *self.addrs
        )


class Dirent:
    """Represents an xv6 on-disk directory entry (struct dirent)."""
    __slots__ = ('inum', 'name')

    def __init__(self, inum: int, name: str):
        self.inum = inum
        self.name = name

    @classmethod
    def unpack(cls, data: bytes) -> 'Dirent':
        inum, raw_name = struct.unpack(DIRENT_FORMAT, data)
        # Directory names are null-terminated or up to DIRSIZ chars
        null_idx = raw_name.find(b'\x00')
        if null_idx != -1:
            raw_name = raw_name[:null_idx]
        name = raw_name.decode('latin1', errors='replace')
        return cls(inum, name)

    def pack(self) -> bytes:
        encoded = self.name.encode('latin1', errors='replace')[:DIRSIZ]
        padded = encoded.ljust(DIRSIZ, b'\x00')
        return struct.pack(DIRENT_FORMAT, self.inum, padded)


def IBLOCK(inum: int, sb: Superblock) -> int:
    """Returns the disk block number containing inode `inum`."""
    return (inum // IPB) + sb.inodestart


def BBLOCK(b: int, sb: Superblock) -> int:
    """Returns the disk block number of the free map containing the bit for block `b`."""
    return (b // BPB) + sb.bmapstart

