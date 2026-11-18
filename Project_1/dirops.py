"""
dirops.py - Directory and namespace operations for xv6.
Matches kernel/fs.c (dirlink) and kernel/sysfile.c (create, sys_mkdir, sys_unlink, sys_link).
"""

import errno
from typing import Optional

from xv6defs import (
    DIRSIZ, DIRENT_SIZE, T_DIR, T_FILE, T_DEVICE,
    Dirent
)
import alloc


def dirlink(img, dp_inum: int, name: str, inum: int) -> int:
    """Add a directory entry (name, inum) to dp_inum, reusing empty slots if available."""
    if not name or '/' in name or '\x00' in name:
        raise OSError(errno.EINVAL, f"Invalid entry name: {name!r}")

    encoded = name.encode('latin1', errors='replace')
    if len(encoded) > DIRSIZ:
        raise OSError(errno.ENAMETOOLONG, f"File name exceeds DIRSIZ ({DIRSIZ}): {name}")

    dp = img.read_dinode(dp_inum)
    if dp.type != T_DIR:
        raise OSError(errno.ENOTDIR, f"Inode {dp_inum} is not a directory")

    # check for duplicates and record first empty slot to reuse
    empty_slot: Optional[int] = None
    off = 0

    while off < dp.size:
        data = img.readi(dp_inum, off, DIRENT_SIZE)
        if len(data) < DIRENT_SIZE:
            break
        de = Dirent.unpack(data)
        if de.inum != 0:
            if de.name == name:
                raise OSError(errno.EEXIST, f"Entry '{name}' already exists in directory {dp_inum}")
        elif empty_slot is None:
            empty_slot = off
        off += DIRENT_SIZE

    target_off = empty_slot if empty_slot is not None else dp.size
    new_de = Dirent(inum, name)
    alloc.writei(img, dp_inum, new_de.pack(), target_off)
    return 0


def isdirempty(img, inum: int) -> bool:
    """Check if directory only contains '.' and '..' entries."""
    dp = img.read_dinode(inum)
    if dp.type != T_DIR:
        raise OSError(errno.ENOTDIR, f"Inode {inum} is not a directory")

    off = 0
    while off < dp.size:
        data = img.readi(inum, off, DIRENT_SIZE)
        if len(data) < DIRENT_SIZE:
            break
        de = Dirent.unpack(data)
        if de.inum != 0 and de.name not in ('.', '..'):
            return False
        off += DIRENT_SIZE

    return True


def create(img, path: str, type_: int = T_FILE, major: int = 0, minor: int = 0) -> int:
    """Create a new file or directory at path and return its inode number."""
    dp_inum, name = img.nameiparent(path)
    if dp_inum is None:
        raise OSError(errno.ENOENT, f"Parent directory does not exist for: {path}")

    if not name or name in ('.', '..'):
        raise OSError(errno.EINVAL, f"Cannot create with name: {name!r}")

    encoded = name.encode('latin1', errors='replace')
    if len(encoded) > DIRSIZ:
        raise OSError(errno.ENAMETOOLONG, f"File name exceeds DIRSIZ ({DIRSIZ}): {name}")

    # in xv6, create on an existing file succeeds without error
    lookup = img.dirlookup(dp_inum, name)
    if lookup is not None:
        existing_inum, _ = lookup
        existing_ip = img.read_dinode(existing_inum)
        if type_ == T_FILE and existing_ip.type in (T_FILE, T_DEVICE):
            return existing_inum
        raise OSError(errno.EEXIST, f"File or directory already exists: {path}")

    ip_inum = alloc.ialloc(img, type_)

    try:
        ip = img.read_dinode(ip_inum)
        ip.type = type_
        ip.major = major
        ip.minor = minor
        ip.nlink = 1
        ip.size = 0
        img.write_dinode(ip_inum, ip)

        if type_ == T_DIR:
            # in xv6 '.' doesn't bump nlink to prevent cycle count issues
            dirlink(img, ip_inum, ".", ip_inum)
            dirlink(img, ip_inum, "..", dp_inum)

        dirlink(img, dp_inum, name, ip_inum)

        if type_ == T_DIR:
            # child's '..' points back to parent, so bump parent's nlink
            dp = img.read_dinode(dp_inum)
            dp.nlink += 1
            img.write_dinode(dp_inum, dp)

        return ip_inum

    except Exception:
        # rollback on failure
        alloc.itrunc(img, ip_inum)
        alloc.ifree(img, ip_inum)
        raise


def mkdir(img, path: str) -> int:
    """Create a new directory at path."""
    return create(img, path, type_=T_DIR, major=0, minor=0)


def unlink(img, path: str) -> int:
    """Unlink file or directory at path, freeing blocks/inode if nlink hits 0."""
    dp_inum, name = img.nameiparent(path)
    if dp_inum is None:
        raise OSError(errno.ENOENT, f"Parent directory does not exist for: {path}")

    if name in ('.', '..'):
        raise OSError(errno.EINVAL, f"Cannot unlink '.' or '..'")

    lookup = img.dirlookup(dp_inum, name)
    if lookup is None:
        raise OSError(errno.ENOENT, f"No such file or directory: {path}")

    ip_inum, off = lookup
    ip = img.read_dinode(ip_inum)

    if ip.nlink < 1:
        raise RuntimeError(f"Corrupt filesystem: inode {ip_inum} has nlink < 1 ({ip.nlink})")

    if ip.type == T_DIR:
        if not isdirempty(img, ip_inum):
            raise OSError(errno.ENOTEMPTY, f"Directory not empty: {path}")

    # clear directory entry in parent directory
    empty_de = b'\x00' * DIRENT_SIZE
    alloc.writei(img, dp_inum, empty_de, off)

    # decrement parent's nlink for the child's '..'
    if ip.type == T_DIR:
        dp = img.read_dinode(dp_inum)
        dp.nlink -= 1
        img.write_dinode(dp_inum, dp)

    ip.nlink -= 1

    if ip.nlink == 0:
        alloc.itrunc(img, ip_inum)
        alloc.ifree(img, ip_inum)
    else:
        img.write_dinode(ip_inum, ip)

    return 0


def link(img, oldpath: str, newpath: str) -> int:
    """Create hard link newpath pointing to oldpath."""
    ip_inum = img.namei(oldpath)
    if ip_inum is None:
        raise OSError(errno.ENOENT, f"Source path does not exist: {oldpath}")

    ip = img.read_dinode(ip_inum)
    if ip.type == T_DIR:
        raise OSError(errno.EPERM, f"Cannot hard link directories: {oldpath}")

    dp_inum, name = img.nameiparent(newpath)
    if dp_inum is None:
        raise OSError(errno.ENOENT, f"Destination parent directory does not exist: {newpath}")

    ip.nlink += 1
    img.write_dinode(ip_inum, ip)

    try:
        dirlink(img, dp_inum, name, ip_inum)
    except Exception:
        # rollback link count on failure
        ip.nlink -= 1
        img.write_dinode(ip_inum, ip)
        raise

    return 0
