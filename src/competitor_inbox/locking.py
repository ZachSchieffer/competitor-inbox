from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .store import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, UnsafeDataRootError


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(state_dir: Path) -> Iterator[None]:
    if state_dir.is_symlink():
        raise UnsafeDataRootError("run-lock directory cannot be a symlink")
    state_dir.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise UnsafeDataRootError("run-lock directory is not a private directory")
    os.chmod(state_dir, PRIVATE_DIR_MODE)
    lock_path = state_dir / "competitor-inbox.lock"
    if lock_path.is_symlink():
        raise UnsafeDataRootError("run-lock file cannot be a symlink")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, PRIVATE_FILE_MODE)
    except OSError as exc:
        raise UnsafeDataRootError("run-lock file could not be opened safely") from exc
    os.fchmod(descriptor, PRIVATE_FILE_MODE)
    handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    locked = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise AlreadyRunning("Another Competitor Inbox run is active") from exc
        yield
    finally:
        try:
            if locked:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
