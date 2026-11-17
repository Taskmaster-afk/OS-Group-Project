"""
xv6fuse.py - FUSE filesystem implementation for mounting xv6 fs.img on Linux.

Translates Linux VFS operations to xv6 image manipulations via XV6Image,
dirops, and alloc.
"""

import os
import sys
import stat
import time
import errno
from typing import Dict, Any

try:
    from fuse import FUSE, FuseOSError, Operations
except ImportError:
    try:
        from mfusepy import FUSE, FuseOSError, Operations
    except ImportError:
        Operations = object
        FuseOSError = OSError
        FUSE = None

from xv6defs import T_DIR, T_FILE
from xv6img import XV6Image
import alloc
import dirops


class XV6Fuse(Operations):
    def __init__(self, img_path: str):
        self.img = XV6Image(img_path)
        self.epoch = int(time.time())

    def getattr(self, path: str, fh: Any = None) -> Dict[str, Any]:
        inum = self.img.namei(path)
        if inum is None:
            raise FuseOSError(errno.ENOENT)

        inode = self.img.read_dinode(inum)
        if inode.type == T_DIR:
            # Linux tools (like find) expect nlink = 2 + subdirs.
            # xv6 stores nlink = 1 + subdirs (does not count '.').
            # We report inode.nlink + 1 to Linux VFS for directories.
            st_nlink = inode.nlink + 1
            st_mode = stat.S_IFDIR | 0o755
        elif inode.type == T_FILE:
            st_nlink = inode.nlink
            st_mode = stat.S_IFREG | 0o644
        else:
            raise FuseOSError(errno.ENOENT)

        return {
            'st_mode': st_mode,
            'st_nlink': st_nlink,
            'st_size': inode.size,
            'st_ctime': self.epoch,
            'st_mtime': self.epoch,
            'st_atime': self.epoch,
            'st_uid': os.getuid() if hasattr(os, 'getuid') else 1000,
            'st_gid': os.getgid() if hasattr(os, 'getgid') else 1000,
        }

    def readdir(self, path: str, fh: Any):
        inum = self.img.namei(path)
        if inum is None:
            raise FuseOSError(errno.ENOENT)

        entries = self.img.get_dirents(inum)
        # Yield unique entry names
        seen = set()
        for _, name in entries:
            if name not in seen:
                seen.add(name)
                yield name

    def read(self, path: str, size: int, offset: int, fh: Any) -> bytes:
        inum = self.img.namei(path)
        if inum is None:
            raise FuseOSError(errno.ENOENT)
        return self.img.readi(inum, offset, size)

    def write(self, path: str, data: bytes, offset: int, fh: Any) -> int:
        inum = self.img.namei(path)
        if inum is None:
            raise FuseOSError(errno.ENOENT)
        try:
            return alloc.writei(self.img, inum, data, offset)
        except OSError as e:
            raise FuseOSError(e.errno)

    def create(self, path: str, mode: int, fi: Any = None) -> int:
        try:
            inum = dirops.create(self.img, path, type_=T_FILE)
            return inum
        except OSError as e:
            raise FuseOSError(e.errno)

    def mkdir(self, path: str, mode: int) -> None:
        try:
            dirops.mkdir(self.img, path)
        except OSError as e:
            raise FuseOSError(e.errno)

    def unlink(self, path: str) -> None:
        try:
            dirops.unlink(self.img, path)
        except OSError as e:
            raise FuseOSError(e.errno)

    def rmdir(self, path: str) -> None:
        try:
            dirops.unlink(self.img, path)
        except OSError as e:
            raise FuseOSError(e.errno)

    def truncate(self, path: str, length: int, fh: Any = None) -> None:
        inum = self.img.namei(path)
        if inum is None:
            raise FuseOSError(errno.ENOENT)
        try:
            alloc.truncate(self.img, inum, length)
        except OSError as e:
            raise FuseOSError(e.errno)

    # Linux VFS metadata compatibility stubs
    def chmod(self, path: str, mode: int) -> int:
        return 0

    def chown(self, path: str, uid: int, gid: int) -> int:
        return 0

    def utimens(self, path: str, times: Any = None) -> int:
        return 0


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 xv6fuse.py <fs.img> <mountpoint> [-f]")
        sys.exit(1)

    img_path = sys.argv[1]
    mountpoint = sys.argv[2]
    foreground = "-f" in sys.argv

    if FUSE is None:
        print("Error: fusepy or mfusepy is not installed. Install with 'pip install fusepy'")
        sys.exit(1)

    print(f"Mounting {img_path} at {mountpoint} (foreground={foreground})...")
    FUSE(XV6Fuse(img_path), mountpoint, nothreads=True, foreground=foreground)


if __name__ == '__main__':
    main()

