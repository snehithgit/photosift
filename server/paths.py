"""Boundaries shared by inventory and export planning."""

import os
import stat
import time
from pathlib import Path, PurePosixPath


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def clean_folder(value):
    if "\\" in value or ":" in value:
        raise ValueError("Use a root-relative POSIX folder")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Folder must stay within the selected root")
    return "" if str(path) == "." else str(path).strip("/")


def checked_path(root, relative=""):
    root = Path(root)
    target = root / clean_folder(relative)
    # Check each component, including ancestors of a registered root (junctions too).
    for part in [target, *target.parents]:
        if is_link(part):
            raise ValueError("Symlink/reparse source paths are not followed")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source escaped its registered root")
    return target


def overlap(a, b):
    # Callers validate symlink-free absolute paths before transactions. Comparing stored
    # boundaries must not probe a potentially disconnected source inside a write lock.
    a, b = Path(os.path.abspath(a)), Path(os.path.abspath(b))
    return a.is_relative_to(b) or b.is_relative_to(a)


def hash_source(root, relative, *, bytes_per_second=32 * 1024 * 1024, on_progress=None):
    import hashlib

    path = checked_path(root, relative)
    before = path.stat()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise OSError("Source identity changed before hashing")
        digest = hashlib.sha256()
        started = last_progress = time.monotonic()
        read_bytes = 0
        while block := source.read(1024 * 1024):
            digest.update(block)
            read_bytes += len(block)
            while (
                bytes_per_second
                and (
                    delay := read_bytes / bytes_per_second
                    - (time.monotonic() - started)
                )
                > 0
            ):
                time.sleep(min(delay, 0.2))
                if on_progress and time.monotonic() - last_progress >= 1:
                    on_progress()
                    last_progress = time.monotonic()
            if on_progress and time.monotonic() - last_progress >= 1:
                on_progress()
                last_progress = time.monotonic()
        after = os.fstat(source.fileno())
    final = checked_path(root, relative).stat()

    def key(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    if key(before) != key(after) or key(after) != key(final):
        raise OSError("Source changed during hashing; retry inventory")
    return digest.hexdigest(), final
