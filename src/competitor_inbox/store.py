"""Private, atomic storage for production ingestion state."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import NormalizedMessage, ParseFailure, SCHEMA_VERSION, SourceEnvelope


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
DATA_SUBDIRECTORIES = ("raw", "state", "ai-cache", "logs", "outputs")


class UnsafeDataRootError(ValueError):
    pass


class StoreLockError(RuntimeError):
    pass


def default_data_root() -> Path:
    return Path.home() / "competitor-inbox-data"


def _inside_git_worktree(path: Path) -> bool:
    current = path.resolve(strict=False)
    for parent in (current, *current.parents):
        if (parent / ".git").exists():
            return True
    return False


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _assert_no_symlink_components(path: Path) -> None:
    """Reject the addressed root or managed child without following it.

    Platform aliases above the requested path, such as macOS ``/tmp``, are
    allowed. The returned root and every write parent are resolved once and all
    later operations use that resolved directory.
    """

    if _is_symlink(_absolute_without_resolving(path)):
        raise UnsafeDataRootError("private data roots and subdirectories cannot be symlinks")


def _assert_contained(root: Path, child: Path) -> None:
    if child != root and root not in child.parents:
        raise UnsafeDataRootError("private data path resolved outside the data root")


def _harden_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UnsafeDataRootError("private data directory could not be opened safely") from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise UnsafeDataRootError("private data path is not a directory")
        os.fchmod(descriptor, PRIVATE_DIR_MODE)
    finally:
        os.close(descriptor)


def _safe_target(path: Path, *, create_parent: bool) -> Path:
    requested = _absolute_without_resolving(path)
    parent = requested.parent
    _assert_no_symlink_components(parent)
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if not parent.is_dir():
        raise UnsafeDataRootError("private file parent is not a directory")
    _assert_no_symlink_components(parent)
    resolved_parent = parent.resolve(strict=True)
    target = resolved_parent / requested.name
    if _is_symlink(target):
        raise UnsafeDataRootError("private files cannot be symlinks")
    return target


def ensure_private_data_root(path: str | os.PathLike[str] | None = None) -> Path:
    requested = Path(path).expanduser() if path is not None else default_data_root()
    requested = _absolute_without_resolving(requested)
    _assert_no_symlink_components(requested)
    requested.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    _assert_no_symlink_components(requested)
    root = requested.resolve(strict=True)
    if _inside_git_worktree(root):
        raise UnsafeDataRootError("production data root cannot be inside a Git worktree")
    _harden_directory(root)
    for name in DATA_SUBDIRECTORIES:
        child = root / name
        if _is_symlink(child):
            raise UnsafeDataRootError("private data subdirectories cannot be symlinks")
        child.mkdir(exist_ok=True, mode=PRIVATE_DIR_MODE)
        if _is_symlink(child):
            raise UnsafeDataRootError("private data subdirectories cannot be symlinks")
        resolved_child = child.resolve(strict=True)
        _assert_contained(root, resolved_child)
        _harden_directory(resolved_child)
    return root


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int = PRIVATE_FILE_MODE) -> None:
    target = _safe_target(path, create_parent=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temp_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_bytes_no_follow(path: Path) -> bytes:
    target = _safe_target(path, create_parent=False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise UnsafeDataRootError("private file could not be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafeDataRootError("private file is not a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json_no_follow(path: Path) -> dict[str, Any]:
    return json.loads(read_bytes_no_follow(path).decode("utf-8"))


def atomic_write_json(path: Path, value: Mapping[str, Any] | list[Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    atomic_write_bytes(path, payload + b"\n")


class MasterStore:
    def __init__(self, data_root: str | os.PathLike[str] | None = None) -> None:
        self.root = ensure_private_data_root(data_root)
        self.master_path = self.root / "master.json"
        self.failure_path = self.root / "state" / "parse-failures.json"

    def save(
        self,
        records: Iterable[NormalizedMessage],
        *,
        failures: Iterable[ParseFailure] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        record_list = list(records)
        failure_list = list(failures)
        document = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "record_count": len(record_list),
            "metadata": dict(metadata or {}),
            "records": [record.to_dict() for record in record_list],
        }
        atomic_write_json(self.master_path, document)
        atomic_write_json(
            self.failure_path,
            {
                "count": len(failure_list),
                "failures": [failure.to_dict() for failure in failure_list],
            },
        )

    def load(self) -> list[NormalizedMessage]:
        if _is_symlink(self.master_path):
            raise UnsafeDataRootError("master data file cannot be a symlink")
        if not self.master_path.exists():
            return []
        document = _read_json_no_follow(self.master_path)
        return [NormalizedMessage.from_dict(value) for value in document.get("records", [])]

    def load_document(self) -> dict[str, Any]:
        if _is_symlink(self.master_path):
            raise UnsafeDataRootError("master data file cannot be a symlink")
        if not self.master_path.exists():
            return {"schema_version": SCHEMA_VERSION, "record_count": 0, "records": []}
        return _read_json_no_follow(self.master_path)

    def save_raw(self, envelope: SourceEnvelope) -> Path:
        legacy_identity = "\0".join(
            (
                envelope.source_type,
                envelope.uidvalidity or "",
                envelope.source_uid,
            )
        )
        source_type, mailbox, uidvalidity, source_uid = envelope.identity_key
        identity = "\0".join((source_type, mailbox, uidvalidity or "", source_uid))
        raw_root = self.root / "raw"
        destination = raw_root / (
            hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest() + ".eml"
        )
        legacy_destination = raw_root / (
            hashlib.sha256(legacy_identity.encode("utf-8", "replace")).hexdigest() + ".eml"
        )
        if _is_symlink(destination) or _is_symlink(legacy_destination):
            raise UnsafeDataRootError("raw message targets cannot be symlinks")
        # Existing private stores used a mailbox-less filename. Reuse it when
        # present so an upgrade does not duplicate every raw message on the first
        # 14-day overlap. New writes are always mailbox-namespaced.
        if not destination.exists() and legacy_destination.exists():
            legacy_digest = hashlib.sha256(read_bytes_no_follow(legacy_destination)).digest()
            incoming_digest = hashlib.sha256(envelope.raw_bytes).digest()
            if legacy_digest == incoming_digest:
                return legacy_destination
        if not destination.exists():
            atomic_write_bytes(destination, envelope.raw_bytes)
        return destination

    def read_state(self, name: str) -> dict[str, Any]:
        path = self._state_path(name)
        if _is_symlink(path):
            raise UnsafeDataRootError("state files cannot be symlinks")
        if not path.exists():
            return {}
        return _read_json_no_follow(path)

    def write_state(self, name: str, value: Mapping[str, Any]) -> None:
        atomic_write_json(self._state_path(name), dict(value))

    def _state_path(self, name: str) -> Path:
        safe_name = "".join(character for character in name if character.isalnum() or character in "-_")
        if not safe_name or safe_name != name:
            raise ValueError("state name contains unsupported characters")
        return self.root / "state" / f"{safe_name}.json"


class StoreLock(AbstractContextManager["StoreLock"]):
    """A non-blocking inter-process lock backed by ``flock`` on macOS/Linux."""

    def __init__(self, data_root: str | os.PathLike[str] | None = None) -> None:
        self.root = ensure_private_data_root(data_root)
        self.path = self.root / "state" / "ingestion.lock"
        self._handle: Any = None

    def __enter__(self) -> "StoreLock":
        import fcntl

        target = _safe_target(self.path, create_parent=False)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags, PRIVATE_FILE_MODE)
        except OSError as exc:
            raise UnsafeDataRootError("ingestion lock could not be opened safely") from exc
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise StoreLockError("another ingestion run is active") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
