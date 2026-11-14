# Team Reading List

Chapter names below are from the current RISC-V xv6 book. Numbering differs between editions, so go by the titles. Source files are in the xv6-riscv repo you cloned (`kernel/`, `mkfs/`).

## Everyone

1. **xv6 landing page and book:** https://pdos.csail.mit.edu/6.828/xv6 (links to the latest source and text on GitHub). Read the introduction and the *Operating system organization* chapter. Look for: user space vs. kernel space, and what a syscall is.
2. **`README.md` and `CONTRIBUTING.md`** in this repo. Look for: the safety rules (never mount and boot the same image) and the git workflow.
3. **`xv6defs.py` and the docstrings in `xv6img.py`.** Even the MLFQ pair should skim these so they can explain the FUSE half in the demo.

## FUSE track (Tejasv, Shaurya, Kushagra, Bharat)

Read in this order.

| # | Read | Look for |
|---|------|----------|
| 1 | xv6 book, **File system** chapter (inodes, directories, path names; skim the logging section) | The layers: disk, buffer cache, logging, inodes, directories, paths. We bypass the log. |
| 2 | `kernel/fs.h` (about 60 lines) | Where every number in `xv6defs.py` comes from. |
| 3 | `kernel/fs.c`: `balloc`, `bfree`, `ialloc`, `bmap`, `itrunc`, `readi`, `writei`, `dirlookup`, `dirlink` | The reference for your Python code. Ignore `begin_op`, `log_write`, locks, `iget`/`iput`. |
| 4 | `mkfs/mkfs.c` | How the image is first built: why the root directory is padded to 1024 bytes, why metadata blocks are already marked in the bitmap. |
| 5 | fusepy README and `examples/memory.py`: https://github.com/fusepy/fusepy | What `getattr`, `readdir`, `read`, `write` must return. `memory.py` is the closest template for `xv6fuse.py`. |

Role-specific extras:

- **Tejasv:** study `getattr` in the fusepy example closely, since it decides how `ls -l` looks.
- **Shaurya:** the bitmap macros in `fs.h`, plus `itrunc` and `writei` in `fs.c`.
- **Kushagra:** `sys_mkdir`, `sys_unlink` and `create` in `kernel/sysfile.c`. They show the nlink rules, including why `.` is not counted.
- **Bharat:** `mkfs.c` again, since it is your ground truth for a valid image. Also skim the book's crash-recovery section to see why bitmap and inode consistency matter, which is what fsck checks.

## MLFQ track (Rohit, Sarthak)

| # | Read | Look for |
|---|------|----------|
| 1 | OSTEP, *Scheduling: The Multi-Level Feedback Queue* (free at https://pages.cs.wisc.edu/~remzi/OSTEP/) | The rules, plus the starvation and gaming problems. This is your assignment. |
| 2 | xv6 book, **Scheduling** chapter | `scheduler()`, `sched()`, `yield()`, the context switch. |
| 3 | xv6 book, **Traps** chapter (timer interrupt part) | How a tick reaches `usertrap()` and `kerneltrap()`. |
| 4 | `kernel/proc.c`: `allocproc`, `scheduler`, `yield`, `sleep`, `wakeup` | Where per-process fields are initialised; what "gave up the CPU early" looks like in code. |
| 5 | `kernel/trap.c` and one simple existing syscall (`getpid` or `uptime`) | The path of a syscall through `syscall.h`, `syscall.c`, `sysproc.c`, `user.h`, `usys.pl`. |

## If you only have time for three files

- **FUSE:** `kernel/fs.h`, `kernel/fs.c`, `mkfs/mkfs.c`
- **MLFQ:** `kernel/proc.c`, `kernel/trap.c`, `kernel/proc.h`