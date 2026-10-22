"""One Python API for calendars and task lists.

The I/O classes come in two modes: ``Workspace`` / ``AsyncWorkspace``,
``Backend`` / ``AsyncBackend``, ``Collection`` / ``AsyncCollection``.  Items
are mode-free data (``Task``, ``Event``, ``Journal``); what a collection hands
out is bound to it (``SyncTask`` / ``AsyncTask`` and so on).
"""

try:
    from ._version import __version__
except ImportError:
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("calendaring")
    except PackageNotFoundError:
        __version__ = "0+unknown"

from ._async.backend import AsyncBackend
from ._async.collection import AsyncCollection
from ._async.items import AsyncEvent, AsyncItem, AsyncJournal, AsyncTask
from ._async.workspace import AsyncWorkspace
from ._sync.backend import Backend
from ._sync.collection import Collection
from ._sync.items import SyncEvent, SyncItem, SyncJournal, SyncTask
from ._sync.workspace import Workspace
from .capabilities import Capabilities, Capability, Feature, Support
from .errors import (
    AlreadyExistsError,
    AmbiguousError,
    AuthenticationError,
    AuthorizationError,
    BackendError,
    CalendaringError,
    CalendaringWarning,
    ConfigurationError,
    ConflictError,
    InvalidDataError,
    LossyWriteError,
    LossyWriteWarning,
    NotFoundError,
    PartialResultError,
    RateLimitError,
    SyncTokenError,
    TransportError,
    UnsupportedError,
    VerificationError,
)
from .items import Event, Item, Journal, Task, TaskStatus
from .loss import Loss, LossPolicy
from .types import BrokenObject, ChangeSet, Component, MultiResult, Scope, StoredObject, SyncToken

# Register the reference backend's kind ("memory") in both modes.
from . import memory as _memory  # noqa: E402, F401  isort: skip

__all__ = [
    "AlreadyExistsError",
    "AmbiguousError",
    "AsyncBackend",
    "AsyncCollection",
    "AsyncEvent",
    "AsyncItem",
    "AsyncJournal",
    "AsyncTask",
    "AsyncWorkspace",
    "AuthenticationError",
    "AuthorizationError",
    "Backend",
    "BackendError",
    "CalendaringError",
    "CalendaringWarning",
    "Capabilities",
    "Capability",
    "ChangeSet",
    "Collection",
    "Component",
    "ConfigurationError",
    "ConflictError",
    "Event",
    "Feature",
    "InvalidDataError",
    "Item",
    "Journal",
    "Loss",
    "LossPolicy",
    "LossyWriteError",
    "LossyWriteWarning",
    "MultiResult",
    "NotFoundError",
    "PartialResultError",
    "RateLimitError",
    "Scope",
    "BrokenObject",
    "StoredObject",
    "Support",
    "SyncEvent",
    "SyncItem",
    "SyncJournal",
    "SyncTask",
    "SyncToken",
    "SyncTokenError",
    "Task",
    "TaskStatus",
    "TransportError",
    "UnsupportedError",
    "VerificationError",
    "Workspace",
    "__version__",
]
