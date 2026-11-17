"""
dirops.py - Namespace operations for xv6 filesystem: directory entries, file/directory creation, and unlinking.

This module is the core responsibility of Kushagra (F3: Namespace operations).
It implements:
  - dirlink: Write a new directory entry (reusing empty slots when available).
  - isdirempty: Verify if a directory contains only '.' and '..'.
  - create: Core inode creation logic for files and directories with proper xv6 nlink conventions.
  - mkdir: Create a new directory and populate '.' and '..' without cyclic ref counts.
  - unlink: Remove a directory entry, decrement link counts, and reclaim blocks/inodes.

Corresponds directly to:
  - kernel/fs.c: dirlink
  - kernel/sysfile.c: create, sys_mkdir, sys_unlink, isdirempty
"""

import errno
from typing import Optional

from xv6defs import (
    DIRSIZ, DIRENT_SIZE, T_DIR, T_FILE, T_DEVICE,
    Dirent
)
import alloc


def dirlink(img, dp_inum: int, name: str, inum: int) -> int:
    """
    Write a new directory entry (name, inum) into directory `dp_inum`.

    Algorithm (from kernel/fs.c: dirlink):
      1. Check that name does not already exist in dp_inum. If found, raise EEXIST.
      2. Scan through dp's entries in chunks of DIRENT_SIZE (16 bytes):
         Look for an empty slot (de.inum == 0).
      3. If an empty slot is found at `off < dp.size`, reuse it.
         Otherwise, off == dp.size (append at end of directory).
      4. Write the dirent (inum, name) at offset `off` via writei.

    Parameters:
      img: XV6Image instance
      dp_inum: Parent directory inode number
      name: File or directory name (must be <= DIRSIZ = 14 characters)
      inum: Inode number to link

    Returns:
      0 on success.

    Raises:
      OSError(ENAMETOOLONG): If name exceeds DIRSIZ (14 bytes).
      OSError(EINVAL): If name is empty or contains '/'.
      OSError(EEXIST): If entry already exists.
    """
    if not name or '/' in name or '\x00' in name:
        raise OSError(errno.EINVAL, f"Invalid entry name: {name!r}")

    encoded_name = name.encode('latin1', errors='replace')
    if len(encoded_name) > DIRSIZ:
        raise OSError(errno.ENAMETOOLONG, f"File name exceeds DIRSIZ ({DIRSIZ}): {name}")

    dp = img.read_dinode(dp_inum)
    if dp.type != T_DIR:
        raise OSError(errno.ENOTDIR, f"Inode {dp_inum} is not a directory")

    # Step 1: Check that name is not already present, and search for an empty slot (inum == 0)
    empty_slot_off: Optional[int] = None
    off = 0

    while off < dp.size:
        data = img.readi(dp_inum, off, DIRENT_SIZE)
        if len(data) < DIRENT_SIZE:
            break
        de = Dirent.unpack(data)
        if de.inum != 0:
            if de.name == name:
                raise OSError(errno.EEXIST, f"Entry '{name}' already exists in directory {dp_inum}")
        elif empty_slot_off is None:
            # First empty slot found; remember offset to reuse
            empty_slot_off = off
        off += DIRENT_SIZE

    # Step 2: Determine target offset
    target_off = empty_slot_off if empty_slot_off is not None else dp.size

    # Step 3: Write new dirent
    new_de = Dirent(inum, name)
    alloc.writei(img, dp_inum, new_de.pack(), target_off)
    return 0


def isdirempty(img, inum: int) -> bool:
    """
    Check if directory `inum` is empty (kernel/sysfile.c: isdirempty).

    In xv6, a directory is empty if all its entries other than '.' and '..'
    have inum == 0.

    Parameters:
      img: XV6Image instance
      inum: Inode number of the directory to check

    Returns:
      True if directory contains only '.' and '..' (or empty slots), False otherwise.
    """
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
    """
    Create a new file or directory at `path` (kernel/sysfile.c: create).

    xv6 nlink semantics:
      - When a file is created: ip.nlink = 1.
      - When a directory is created:
          - ip.nlink = 1 (NOT 2). '.' does NOT increment ip.nlink to avoid cyclic references.
          - dp.nlink is incremented by 1 because '..' in the child points back to dp.
          - Thus in xv6, a directory's nlink is: 1 + (number of subdirectories).

    Parameters:
      img: XV6Image instance
      path: Path to the new file or directory (e.g. "/foo/bar")
      type_: T_FILE, T_DIR, or T_DEVICE
      major: Major device number (for T_DEVICE)
      minor: Minor device number (for T_DEVICE)

    Returns:
      Inode number of the created (or opened) file.

    Raises:
      OSError(ENOENT): If parent directory does not exist.
      OSError(EEXIST): If file/directory already exists and cannot be opened.
      OSError(ENAMETOOLONG): If basename exceeds DIRSIZ.
      OSError(EINVAL): If name is invalid (e.g. '.' or '..').
      OSError(ENOSPC): If no free inodes or blocks remain.
    """
    dp_inum, name = img.nameiparent(path)
    if dp_inum is None:
        raise OSError(errno.ENOENT, f"Parent directory does not exist for: {path}")

    if not name or name in ('.', '..'):
        raise OSError(errno.EINVAL, f"Cannot create with name: {name!r}")

    encoded_name = name.encode('latin1', errors='replace')
    if len(encoded_name) > DIRSIZ:
        raise OSError(errno.ENAMETOOLONG, f"File name exceeds DIRSIZ ({DIRSIZ}): {name}")

    # Check if entry already exists in parent directory
    lookup_res = img.dirlookup(dp_inum, name)
    if lookup_res is not None:
        existing_inum, _ = lookup_res
        existing_inode = img.read_dinode(existing_inum)
        # In xv6, open with O_CREATE on an existing regular file succeeds
        if type_ == T_FILE and existing_inode.type in (T_FILE, T_DEVICE):
            return existing_inum
        raise OSError(errno.EEXIST, f"File or directory already exists: {path}")

    # Allocate new inode
    ip_inum = alloc.ialloc(img, type_)

    try:
        # Initialize inode fields
        ip = img.read_dinode(ip_inum)
        ip.type = type_
        ip.major = major
        ip.minor = minor
        ip.nlink = 1  # Standard xv6 initial link count
        ip.size = 0
        img.write_dinode(ip_inum, ip)

        if type_ == T_DIR:
            # Create '.' and '..' entries in new directory
            # CRITICAL XV6 RULE:
            # No ip.nlink++ for '.': avoids cyclic reference counts.
            dirlink(img, ip_inum, ".", ip_inum)
            dirlink(img, ip_inum, "..", dp_inum)

        # Link new inode into parent directory
        dirlink(img, dp_inum, name, ip_inum)

        if type_ == T_DIR:
            # Now that child directory link succeeded:
            # Increment parent's nlink for the child's '..' pointer
            dp = img.read_dinode(dp_inum)
            dp.nlink += 1
            img.write_dinode(dp_inum, dp)

        return ip_inum

    except Exception:
        # Rollback on failure
        alloc.itrunc(img, ip_inum)
        alloc.ifree(img, ip_inum)
        raise


def mkdir(img, path: str) -> int:
    """
    Create a new directory at `path` (kernel/sysfile.c: sys_mkdir).

    Delegates to create with type_ = T_DIR.

    Parameters:
      img: XV6Image instance
      path: Path for the new directory

    Returns:
      Inode number of the newly created directory.
    """
    return create(img, path, type_=T_DIR, major=0, minor=0)


def unlink(img, path: str) -> int:
    """
    Unlink (remove) a file or directory at `path` (kernel/sysfile.c: sys_unlink).

    Steps:
      1. Resolve parent directory and target name.
      2. Verify name is not '.' or '..' (cannot unlink '.' or '..').
      3. Look up target in parent directory.
      4. If target is a directory, verify it is empty (isdirempty).
      5. Clear the directory entry in parent directory (overwrite with zeroes).
      6. If target is a directory, decrement parent's nlink (for child's '..').
      7. Decrement target's nlink.
      8. If target's nlink reaches 0, free its data blocks (itrunc) and inode (ifree).

    Parameters:
      img: XV6Image instance
      path: Path to unlink

    Returns:
      0 on success.

    Raises:
      OSError(ENOENT): If path or parent does not exist.
      OSError(EINVAL): If attempting to unlink '.' or '..'.
      OSError(ENOTEMPTY): If attempting to unlink a non-empty directory.
    """
    dp_inum, name = img.nameiparent(path)
    if dp_inum is None:
        raise OSError(errno.ENOENT, f"Parent directory does not exist for: {path}")

    # Cannot unlink "." or ".."
    if name in ('.', '..'):
        raise OSError(errno.EINVAL, f"Cannot unlink '.' or '..'")

    lookup_res = img.dirlookup(dp_inum, name)
    if lookup_res is None:
        raise OSError(errno.ENOENT, f"No such file or directory: {path}")

    ip_inum, off = lookup_res
    ip = img.read_dinode(ip_inum)

    if ip.nlink < 1:
        raise RuntimeError(f"Corrupt filesystem: inode {ip_inum} has nlink < 1 ({ip.nlink})")

    # If directory, ensure it is empty
    if ip.type == T_DIR:
        if not isdirempty(img, ip_inum):
            raise OSError(errno.ENOTEMPTY, f"Directory not empty: {path}")

    # Step 5: Clear directory entry in parent directory (zero out the 16-byte dirent)
    empty_dirent = b'\x00' * DIRENT_SIZE
    alloc.writei(img, dp_inum, empty_dirent, off)

    # Step 6: If it was a directory, decrement parent's nlink (child's '..' is gone)
    if ip.type == T_DIR:
        dp = img.read_dinode(dp_inum)
        dp.nlink -= 1
        img.write_dinode(dp_inum, dp)

    # Step 7: Decrement target's nlink
    ip.nlink -= 1

    # Step 8: If nlink reaches 0, reclaim blocks and inode
    if ip.nlink == 0:
        alloc.itrunc(img, ip_inum)
        alloc.ifree(img, ip_inum)
    else:
        # Inode still has links (e.g. hard links if supported)
        img.write_dinode(ip_inum, ip)

    return 0


def link(img, oldpath: str, newpath: str) -> int:
    """
    Create a hard link `newpath` pointing to existing file `oldpath` (kernel/sysfile.c: sys_link).

    Rules (from xv6):
      - Cannot hard link directories (raises EPERM).
      - Target must exist (raises ENOENT).
      - Destination parent must exist (raises ENOENT).
      - Increment target inode's nlink.
      - Write new directory entry in destination directory.

    Parameters:
      img: XV6Image instance
      oldpath: Existing file path
      newpath: New link path

    Returns:
      0 on success.
    """
    ip_inum = img.namei(oldpath)
    if ip_inum is None:
        raise OSError(errno.ENOENT, f"Source path does not exist: {oldpath}")

    ip = img.read_dinode(ip_inum)
    if ip.type == T_DIR:
        raise OSError(errno.EPERM, f"Cannot hard link directories: {oldpath}")

    dp_inum, name = img.nameiparent(newpath)
    if dp_inum is None:
        raise OSError(errno.ENOENT, f"Destination parent directory does not exist: {newpath}")

    # Step 1: Increment target inode nlink
    ip.nlink += 1
    img.write_dinode(ip_inum, ip)

    # Step 2: Link into parent directory
    try:
        dirlink(img, dp_inum, name, ip_inum)
    except Exception:
        # Roll back link count on failure
        ip.nlink -= 1
        img.write_dinode(ip_inum, ip)
        raise

    return 0

