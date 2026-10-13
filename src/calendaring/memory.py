"""The in-memory reference backend, in both modes, and its capability profiles.

``FULL`` can do everything a backend can declare; ``TRACKER`` is shaped like
an issue tracker; ``MINIMAL`` is writable but leaves out most optional
features; ``FEED`` is read-only.
"""

from ._async.memory import AsyncMemoryBackend, AsyncMemoryCollection
from ._memory_store import FEED, FULL, MINIMAL, TRACKER, MemoryCalendar, MemoryObject, MemoryStore, Profile
from ._sync.memory import MemoryBackend, MemoryCollection

__all__ = [
    "FEED",
    "FULL",
    "MINIMAL",
    "TRACKER",
    "AsyncMemoryBackend",
    "AsyncMemoryCollection",
    "MemoryBackend",
    "MemoryCalendar",
    "MemoryCollection",
    "MemoryObject",
    "MemoryStore",
    "Profile",
]
