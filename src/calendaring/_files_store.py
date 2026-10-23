"""The mode-free storage behind the files backend (roadmap 2.2): a vdir, or one ``.ics`` file.

Both modes' ``FilesBackend`` classes wrap this; it does blocking file I/O and
no parsing beyond what it needs to find UIDs and split a file into objects.
A vdir follows the vdir spec (https://vdirsyncer.pimutils.org/en/stable/vdir.html),
so khal, todoman and vdirsyncer can share it: one object per ``.ics`` file,
collection metadata in plain ``displayname`` and ``color`` files.  Etags are
API_DESIGN §7's: ``f"{st_mtime_ns};{st_ino}"`` for a vdir file, a hash of the
object's serialisation in a single file.  Writes go to a temporary file that
replaces the target (the target of a symlink, keeping its mode); a vdir create
links the temporary file into place, so it never replaces another writer's
object, and falls back to an exclusive create where hard links are not
available (FAT, exFAT, many SMB and FUSE mounts), which is not atomic.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import stat
import uuid
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

import filelock
import icalendar

from .errors import AlreadyExistsError, ConfigurationError, ConflictError, InvalidDataError, NotFoundError
from .items import _items_of, _parse
from .types import Component

__all__ = ["Entry", "IcsFile", "Vdir", "collections", "file_name", "layout", "make_collection"]

# A UID made of these is its own file name, as vdirsyncer names files; any other gets a random one.
SAFE_UID = re.compile(r"[A-Za-z0-9@_-][A-Za-z0-9@._-]{0,63}")
SUFFIX = ".ics"
METADATA = ("displayname", "color")
# The components a collection was made for, where that is not all of them.  The vdir spec has no
# such file (CalDAV's supported-calendar-component-set); other tools ignore a hidden one.
COMPONENTS_FILE = ".calendaring-components"
LOCK_TIMEOUT = 30  # seconds; a writer holds the lock only for one read-modify-write


@dataclass(frozen=True)
class Entry:
    """One stored object as bytes: what the backend parses, or reports broken with these bytes."""

    native_id: str
    etag: str
    data: bytes


def layout(path: Path) -> Literal["file", "collection", "root"]:
    """Which layout ``path`` is; ``ConfigurationError`` where it cannot be a calendar.

    A path ending in ``.ics`` is a single file, which need not exist yet.  A
    directory with subdirectories is a root of collections; one holding
    objects or vdir metadata is one collection; an empty one is a root.
    """
    if path.suffix.lower() == SUFFIX and not path.is_dir():
        if not path.parent.is_dir():
            raise ConfigurationError(f"{path.parent} does not exist")
        return "file"
    if not path.exists():
        raise ConfigurationError(f"{path} does not exist")
    if not path.is_dir():
        raise ConfigurationError(f"{path} is neither a directory nor an .ics file")
    children = _children(path)
    if any(p.is_dir() and not p.name.startswith(".") for p in children):
        return "root"
    if any(_is_object(p) or p.name in METADATA for p in children):
        return "collection"
    return "root"


def file_name(uid: str) -> str:
    return uid + SUFFIX if SAFE_UID.fullmatch(uid) else uuid.uuid4().hex + SUFFIX


def collections(root: Path) -> list[Path]:
    return sorted(p for p in _children(root) if p.is_dir() and not p.name.startswith("."))


def make_collection(root: Path, name: str, components: frozenset[Component]) -> Path:
    if not components:
        raise ValueError("a collection holds at least one component")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "collection"
    for n in range(1, 1000):
        path = root / (slug if n == 1 else f"{slug}-{n}")
        try:
            path.mkdir()
        except FileExistsError:
            continue
        Vdir(path).set_name(name)
        if components != frozenset(Component):
            text = "".join(f"{c.value}\n" for c in sorted(components, key=lambda c: c.value))
            _write_atomically(path / COMPONENTS_FILE, text.encode())
        return path
    raise AlreadyExistsError(f"no free directory name for {name!r} in {root}", taken="native_id")


class Vdir:
    """One collection directory: a file per object, named after its UID where the UID is safe."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def name(self) -> str | None:
        return _read_text(self.path / "displayname")

    @property
    def color(self) -> str | None:
        return _read_text(self.path / "color")

    @property
    def components(self) -> frozenset[Component]:
        known = {c.value: c for c in Component}
        text = _read_text(self.path / COMPONENTS_FILE) or ""
        return frozenset(known[line] for line in text.split() if line in known) or frozenset(Component)

    def set_name(self, name: str) -> None:
        _write_atomically(self.path / "displayname", name.encode() + b"\n")

    def remove(self) -> None:
        shutil.rmtree(self.path)

    def etags(self) -> dict[str, str]:
        """Every object's etag, from a ``stat()`` each: nothing is read."""
        out = {}
        for path in _children(self.path):
            if _is_object(path):
                with contextlib.suppress(FileNotFoundError):  # removed while listing
                    out[path.name] = _etag(path.stat())
        return out

    def entries(self) -> list[Entry]:
        out = []
        for native_id in sorted(self.etags()):
            with contextlib.suppress(NotFoundError):
                out.append(self.read(native_id))
        return out

    def read_many(self, native_ids: Iterable[str]) -> list[Entry]:
        """The objects still there, in the order asked for."""
        out = []
        for native_id in native_ids:
            with contextlib.suppress(NotFoundError):
                out.append(self.read(native_id))
        return out

    def read(self, native_id: str) -> Entry:
        path = self._path(native_id)
        try:
            with path.open("rb") as f:
                etag = _etag(os.fstat(f.fileno()))
                return Entry(native_id, etag, f.read())
        except (FileNotFoundError, IsADirectoryError):
            raise NotFoundError(f"no object {native_id!r}") from None

    def create(self, data: bytes, *, native_id: str | None) -> Entry:
        if native_id is not None and not _plain(native_id):
            raise ValueError(f"{native_id!r} is not a file name for an object: a plain name ending in {SUFFIX}")
        uid = _uid(data)
        if any(_uid_or_none(e.data) == uid for e in self.entries()):
            raise AlreadyExistsError(f"UID {uid!r} is taken", taken="uid")
        name = native_id if native_id is not None else file_name(uid)
        while True:
            try:
                _place(self.path / name, data)
                break
            except FileExistsError:
                if native_id is not None:
                    raise AlreadyExistsError(f"{native_id!r} is taken", taken="native_id") from None
                if _uid_or_none(self._read_or_nothing(name)) == uid:  # another writer stored it meanwhile
                    raise AlreadyExistsError(f"UID {uid!r} is taken", taken="uid") from None
                name = uuid.uuid4().hex + SUFFIX
        _sync_directory(self.path)
        return self.read(name)

    def update(self, native_id: str, data: bytes, *, etag: str | None) -> Entry:
        path = _real(self._path(native_id))
        current = self.read(native_id)
        with _temporary(path.parent, data, _mode_of(path)) as tmp:
            if etag is not None and self.read(native_id).etag != etag:
                raise ConflictError(f"{native_id!r} changed since it was read")
            # A coarse clock could give the new file the old mtime, and a freed inode can be reused:
            # move the mtime past the old one so the etag always changes.
            old_mtime = int(current.etag.split(";")[0])
            if tmp.stat().st_mtime_ns <= old_mtime:
                os.utime(tmp, ns=(old_mtime + 1, old_mtime + 1))
            os.replace(tmp, path)
        _sync_directory(path.parent)
        return self.read(native_id)

    def delete(self, native_id: str, *, etag: str | None) -> None:
        current = self.read(native_id)
        if etag is not None and current.etag != etag:
            raise ConflictError(f"{native_id!r} changed since it was read")
        try:
            self._path(native_id).unlink()
        except FileNotFoundError:
            raise NotFoundError(f"no object {native_id!r}") from None
        _sync_directory(self.path)

    def _path(self, native_id: str) -> Path:
        if not _plain(native_id):
            raise NotFoundError(f"no object {native_id!r}")  # nothing outside the directory, hidden, or metadata
        return self.path / native_id

    def _read_or_nothing(self, native_id: str) -> bytes:
        try:
            return self.read(native_id).data
        except NotFoundError:
            return b""


class IcsFile:
    """One ``.ics`` file holding every object; an object is the components of one UID."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = filelock.FileLock(path.parent / f".{path.name}.lock", timeout=LOCK_TIMEOUT)

    @property
    def name(self) -> str | None:
        try:
            calendar = self._load()
        except InvalidDataError:
            return None
        value = calendar.get("X-WR-CALNAME")
        return None if value is None else str(value)

    def etags(self) -> dict[str, str]:
        return {e.native_id: e.etag for e in self.entries()}

    def entries(self) -> list[Entry]:
        """One entry per UID; a file that does not parse is one entry holding all of it."""
        try:
            calendar = self._load()
        except InvalidDataError:
            raw = self.path.read_bytes()
            return [Entry(self.path.name, _hash(raw), raw)]
        return [self._entry(calendar, uid, parts) for uid, parts in _by_uid(calendar).items()]

    def read_many(self, native_ids: Iterable[str]) -> list[Entry]:
        entries = {e.native_id: e for e in self.entries()}
        return [entries[n] for n in native_ids if n in entries]

    def read(self, native_id: str) -> Entry:
        for entry in self.entries():
            if entry.native_id == native_id:
                return entry
        raise NotFoundError(f"no object {native_id!r}")

    def create(self, data: bytes, *, native_id: str | None) -> Entry:
        """Add an object; it is named by its UID, so ``native_id`` (UNSUPPORTED here) is never given."""
        uid = _uid(data)
        with self._lock:
            calendar = self._load_for_writing()
            if uid in _by_uid(calendar):
                raise AlreadyExistsError(f"UID {uid!r} is taken", taken="uid")
            self._put(calendar, _parse(data))
            self._save(calendar)
            return self._entry(calendar, uid, _by_uid(calendar)[uid])

    def update(self, native_id: str, data: bytes, *, etag: str | None) -> Entry:
        with self._lock:
            calendar = self._load_for_writing()
            self._check(calendar, native_id, etag)
            calendar.subcomponents[:] = [c for c in calendar.subcomponents if _uid_of(c) != native_id]
            self._put(calendar, _parse(data))
            self._save(calendar)
            return self._entry(calendar, native_id, _by_uid(calendar)[native_id])

    def delete(self, native_id: str, *, etag: str | None) -> None:
        with self._lock:
            calendar = self._load_for_writing()
            self._check(calendar, native_id, etag)
            calendar.subcomponents[:] = [c for c in calendar.subcomponents if _uid_of(c) != native_id]
            self._save(calendar)

    def _load(self) -> icalendar.Calendar:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            calendar = icalendar.Calendar()
            calendar.add("VERSION", "2.0")
            calendar.add("PRODID", "-//calendaring//files backend//EN")
            return calendar
        return _parse(raw)

    def _load_for_writing(self) -> icalendar.Calendar:
        try:
            return self._load()
        except InvalidDataError as exc:
            raise InvalidDataError(f"{self.path} does not parse; repair it before writing: {exc}") from exc

    def _check(self, calendar: icalendar.Calendar, native_id: str, etag: str | None) -> None:
        parts = _by_uid(calendar).get(native_id)
        if parts is None:
            raise NotFoundError(f"no object {native_id!r}")
        if etag is not None and self._entry(calendar, native_id, parts).etag != etag:
            raise ConflictError(f"{native_id!r} changed since it was read")

    @staticmethod
    def _put(calendar: icalendar.Calendar, item: icalendar.Calendar) -> None:
        """Add an object's components, and those of its time zones the file does not have yet."""
        have = {str(c["TZID"]) for c in calendar.walk("VTIMEZONE")}
        zones = [c for c in item.subcomponents if c.name == "VTIMEZONE" and str(c["TZID"]) not in have]
        first_item = next((i for i, c in enumerate(calendar.subcomponents) if c.name != "VTIMEZONE"), None)
        at = len(calendar.subcomponents) if first_item is None else first_item
        calendar.subcomponents[at:at] = zones
        calendar.subcomponents.extend(c for c in item.subcomponents if c.name != "VTIMEZONE")

    def _save(self, calendar: icalendar.Calendar) -> None:
        """Write the file, without the time zones nothing in it uses any more."""
        used = _tzids(c for c in calendar.subcomponents if c.name != "VTIMEZONE")
        calendar.subcomponents[:] = [
            c for c in calendar.subcomponents if c.name != "VTIMEZONE" or str(c["TZID"]) in used
        ]
        _write_atomically(self.path, calendar.to_ical())

    @staticmethod
    def _entry(calendar: icalendar.Calendar, uid: str, parts: list[icalendar.Component]) -> Entry:
        item = icalendar.Calendar()
        for name in ("VERSION", "PRODID"):
            if name in calendar:
                item.add(name, calendar[name])
        used = _tzids(parts)
        item.subcomponents.extend(z for z in calendar.walk("VTIMEZONE") if str(z["TZID"]) in used)
        item.subcomponents.extend(parts)
        data = item.to_ical()
        return Entry(uid, _hash(data), data)


def _by_uid(calendar: icalendar.Calendar) -> dict[str, list[icalendar.Component]]:
    out: dict[str, list[icalendar.Component]] = {}
    for component in calendar.subcomponents:
        uid = _uid_of(component)
        if uid is not None:
            out.setdefault(uid, []).append(component)
    return out


def _uid_of(component: icalendar.Component) -> str | None:
    if component.name not in ("VEVENT", "VTODO", "VJOURNAL") or "UID" not in component:
        return None
    return str(component["UID"])


def _tzids(components: Iterable[icalendar.Component]) -> set[str]:
    """The ``TZID`` parameters the components and their subcomponents use, list values (``EXDATE``) included."""
    out = set()
    for component in components:
        for part in component.walk():
            for value in part.values():
                for one in value if isinstance(value, list) else [value]:
                    tzid = getattr(one, "params", {}).get("TZID")
                    if tzid:
                        out.add(str(tzid))
    return out


def _uid(data: bytes) -> str:
    return str(_items_of(_parse(data))[0]["UID"])


def _uid_or_none(data: bytes) -> str | None:
    try:
        return _uid(data)
    except (InvalidDataError, KeyError, IndexError):
        return None


def _children(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []


def _is_object(path: Path) -> bool:
    return _plain(path.name) and path.is_file()


def _plain(native_id: str) -> bool:
    """A file name in the directory, not hidden, ending in ``.ics``: on Windows too (``C:x.ics``, ``a\\b.ics``)."""
    return (
        native_id == Path(native_id).name == PureWindowsPath(native_id).name
        and not native_id.startswith(".")
        and native_id.lower().endswith(SUFFIX)
        and len(native_id) > len(SUFFIX)
    )


def _etag(stat: os.stat_result) -> str:
    return f"{stat.st_mtime_ns};{stat.st_ino}"


def _hash(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


def _write_atomically(path: Path, data: bytes) -> None:
    """Replace ``path`` (a symlink's target) with ``data``, keeping its mode."""
    target = _real(path)
    with _temporary(target.parent, data, _mode_of(target)) as tmp:
        os.replace(tmp, target)
    _sync_directory(target.parent)


def _place(target: Path, data: bytes) -> None:
    """Create ``target`` holding ``data``; ``FileExistsError`` if it exists, which is never overwritten."""
    with _temporary(target.parent, data, None) as tmp:
        try:
            os.link(tmp, target)
            return
        except FileExistsError:
            raise
        except OSError:  # no hard links here (FAT, exFAT, many SMB and FUSE mounts)
            pass
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


@contextlib.contextmanager
def _temporary(directory: Path, data: bytes, mode: int | None) -> Generator[Path]:
    """``data`` in a new hidden file in ``directory``, flushed to disk; removed unless renamed away.

    It gets ``mode``, or else what the umask leaves of 0666, as any new file would.
    """
    while True:
        tmp = directory / f".{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            break
        except FileExistsError:
            continue
    try:
        with os.fdopen(fd, "wb") as f:
            if mode is not None:
                os.chmod(tmp, mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)


def _real(path: Path) -> Path:
    """What to replace: a symlink's target, so the link stays."""
    return path.resolve() if path.is_symlink() else path


def _mode_of(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def _sync_directory(path: Path) -> None:
    """Make a rename, link or removal in ``path`` survive a power cut; Windows cannot sync a directory."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
