"""
alloc.py - Bitmap, block, and inode allocation and file write operations for xv6.

Corresponds to kernel/fs.c: balloc, bfree, ialloc, itrunc, bmap, writei.
"""

import errno
import struct
from xv6defs import (
    BSIZE, BPB, NDIRECT, NINDIRECT, MAXFILE,
    T_UNUSED, Dinode, BBLOCK
)


def balloc(img) -> int:
    """
    Allocate a zeroed disk block from the free bitmap.
    Returns the block number allocated.
    Raises OSError(ENOSPC) if no free blocks remain.
    """
    sb = img.sb
    # Scan all blocks in the file system
    for b in range(0, sb.size, BPB):
        bblock = BBLOCK(b, sb)
        bitmap_data = bytearray(img.read_block(bblock))
        
        limit = min(BPB, sb.size - b)
        for bi in range(limit):
            byte_idx = bi // 8
            bit_idx = bi % 8
            mask = 1 << bit_idx
            
            if (bitmap_data[byte_idx] & mask) == 0:
                # Mark as allocated
                bitmap_data[byte_idx] |= mask
                img.write_block(bblock, bytes(bitmap_data))
                
                allocated_block = b + bi
                # Zero out the allocated block
                img.write_block(allocated_block, b'\x00' * BSIZE)
                return allocated_block
                
    raise OSError(errno.ENOSPC, "No free data blocks available")


def bfree(img, b: int) -> None:
    """
    Free a disk block by clearing its bit in the bitmap.
    """
    sb = img.sb
    if b <= 0 or b >= sb.size:
        raise ValueError(f"Block number out of range: {b}")
        
    bblock = BBLOCK(b, sb)
    bitmap_data = bytearray(img.read_block(bblock))
    
    bi = b % BPB
    byte_idx = bi // 8
    bit_idx = bi % 8
    mask = ~(1 << bit_idx) & 0xFF
    
    bitmap_data[byte_idx] &= mask
    img.write_block(bblock, bytes(bitmap_data))


def ialloc(img, type_: int) -> int:
    """
    Allocate a free inode from disk, setting its type and zeroing fields.
    Returns the inode number allocated.
    Raises OSError(ENOSPC) if all inodes are in use.
    """
    sb = img.sb
    for inum in range(1, sb.ninodes):
        inode = img.read_dinode(inum)
        if inode.type == T_UNUSED:
            # Found a free inode. Initialize and commit to disk.
            inode.type = type_
            inode.major = 0
            inode.minor = 0
            inode.nlink = 0
            inode.size = 0
            inode.addrs = [0] * (NDIRECT + 1)
            img.write_dinode(inum, inode)
            return inum
            
    raise OSError(errno.ENOSPC, "No free inodes available")


def ifree(img, inum: int) -> None:
    """
    Mark an inode on disk as free (T_UNUSED).
    """
    inode = Dinode()
    img.write_dinode(inum, inode)


def bmap(img, inode: Dinode, inum: int, bn: int, alloc: bool = False) -> int:
    """
    Map logical block number `bn` of `inode` to a physical disk block number.
    If `alloc` is True, unmapped blocks are allocated via `balloc`.
    If block is unallocated and `alloc` is False, returns 0.
    """
    if bn < 0 or bn >= MAXFILE:
        raise ValueError(f"Block number out of range: {bn}")

    # Direct block
    if bn < NDIRECT:
        addr = inode.addrs[bn]
        if addr == 0:
            if not alloc:
                return 0
            addr = balloc(img)
            inode.addrs[bn] = addr
            img.write_dinode(inum, inode)
        return addr

    # Indirect block
    bn -= NDIRECT
    if bn < NINDIRECT:
        indir_addr = inode.addrs[NDIRECT]
        if indir_addr == 0:
            if not alloc:
                return 0
            indir_addr = balloc(img)
            inode.addrs[NDIRECT] = indir_addr
            img.write_dinode(inum, inode)

        indir_data = bytearray(img.read_block(indir_addr))
        addr = struct.unpack_from("<I", indir_data, bn * 4)[0]
        if addr == 0:
            if not alloc:
                return 0
            addr = balloc(img)
            struct.pack_into("<I", indir_data, bn * 4, addr)
            img.write_block(indir_addr, bytes(indir_data))
        return addr

    raise ValueError(f"Block number exceeds maximum file size: {bn}")


def itrunc(img, inum: int) -> None:
    """
    Free all data blocks associated with inode `inum`, clearing addrs and size.
    """
    inode = img.read_dinode(inum)
    
    # Free direct blocks
    for i in range(NDIRECT):
        if inode.addrs[i] != 0:
            bfree(img, inode.addrs[i])
            inode.addrs[i] = 0

    # Free indirect blocks
    indir_addr = inode.addrs[NDIRECT]
    if indir_addr != 0:
        indir_data = img.read_block(indir_addr)
        for j in range(NINDIRECT):
            addr = struct.unpack_from("<I", indir_data, j * 4)[0]
            if addr != 0:
                bfree(img, addr)
        bfree(img, indir_addr)
        inode.addrs[NDIRECT] = 0

    inode.size = 0
    img.write_dinode(inum, inode)


def writei(img, inum: int, data: bytes, offset: int) -> int:
    """
    Write data into inode `inum` starting at `offset`.
    Allocates blocks as needed. Updates file size if written past EOF.
    Returns the number of bytes written.
    """
    if offset < 0:
        raise ValueError("Negative offset")

    total_len = len(data)
    if offset + total_len > MAXFILE * BSIZE:
        raise OSError(errno.EFBIG, "File exceeds maximum xv6 file size")

    inode = img.read_dinode(inum)
    written = 0

    while written < total_len:
        curr_offset = offset + written
        bn = curr_offset // BSIZE
        boff = curr_offset % BSIZE
        chunk_len = min(total_len - written, BSIZE - boff)

        # Allocate or retrieve disk block
        pblock = bmap(img, inode, inum, bn, alloc=True)

        block_data = bytearray(img.read_block(pblock))
        block_data[boff:boff + chunk_len] = data[written:written + chunk_len]
        img.write_block(pblock, bytes(block_data))

        written += chunk_len

    if offset + written > inode.size:
        inode.size = offset + written
        img.write_dinode(inum, inode)

    return written


def truncate(img, inum: int, new_size: int) -> None:
    """
    Truncate or extend file `inum` to `new_size` bytes.
    """
    if new_size < 0:
        raise ValueError("Negative size")
    if new_size > MAXFILE * BSIZE:
        raise OSError(errno.EFBIG, "File exceeds maximum xv6 file size")

    inode = img.read_dinode(inum)
    if new_size == 0:
        itrunc(img, inum)
        return

    if new_size < inode.size:
        # Free blocks entirely beyond new_size
        first_freed_block = (new_size + BSIZE - 1) // BSIZE
        old_last_block = (inode.size + BSIZE - 1) // BSIZE
        
        for bn in range(first_freed_block, old_last_block):
            if bn < NDIRECT:
                if inode.addrs[bn] != 0:
                    bfree(img, inode.addrs[bn])
                    inode.addrs[bn] = 0
            else:
                indir_idx = bn - NDIRECT
                if inode.addrs[NDIRECT] != 0:
                    indir_data = bytearray(img.read_block(inode.addrs[NDIRECT]))
                    addr = struct.unpack_from("<I", indir_data, indir_idx * 4)[0]
                    if addr != 0:
                        bfree(img, addr)
                        struct.pack_into("<I", indir_data, indir_idx * 4, 0)
                        img.write_block(inode.addrs[NDIRECT], bytes(indir_data))

        # If indirect block itself becomes completely empty, free it
        if inode.addrs[NDIRECT] != 0 and first_freed_block <= NDIRECT:
            indir_data = img.read_block(inode.addrs[NDIRECT])
            if all(addr == 0 for addr, in struct.iter_unpack("<I", indir_data)):
                bfree(img, inode.addrs[NDIRECT])
                inode.addrs[NDIRECT] = 0

        # Zero out the tail of the last partial block
        partial_off = new_size % BSIZE
        if partial_off != 0:
            last_bn = new_size // BSIZE
            pblock = bmap(img, inode, inum, last_bn, alloc=False)
            if pblock != 0:
                block_data = bytearray(img.read_block(pblock))
                block_data[partial_off:] = b'\x00' * (BSIZE - partial_off)
                img.write_block(pblock, bytes(block_data))

        inode.size = new_size
        img.write_dinode(inum, inode)
    elif new_size > inode.size:
        # Zero-pad to new_size
        pad_len = new_size - inode.size
        zero_buf = b'\x00' * min(pad_len, BSIZE)
        curr = inode.size
        while curr < new_size:
            step = min(new_size - curr, len(zero_buf))
            writei(img, inum, zero_buf[:step], curr)
            curr += step

