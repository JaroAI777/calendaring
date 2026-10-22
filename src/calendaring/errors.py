"""The exception hierarchy (API_DESIGN.md §6).

Every error carries ``.backend`` (the backend's id) and, where known,
``.collection_id``.  A backend raises these ``from`` its own exception, so the
native one is always ``__cause__``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .capabilities import Feature
    from .loss import Loss
    from .types import BrokenObject

__all__ = [
    "AlreadyExistsError",
    "AmbiguousError",
    "AuthenticationError",
    "AuthorizationError",
    "BackendError",
    "CalendaringError",
    "CalendaringWarning",
    "ConfigurationError",
    "ConflictError",
    "InvalidDataError",
    "LossyWriteError",
    "LossyWriteWarning",
    "NotFoundError",
    "PartialResultError",
    "RateLimitError",
    "SyncTokenError",
    "TransportError",
    "UnsupportedError",
    "VerificationError",
]


class CalendaringError(Exception):
    """The base of every error this library raises."""

    def __init__(self, message: str = "", *, backend: str | None = None, collection_id: str | None = None) -> None:
        super().__init__(message)
        self.backend = backend
        self.collection_id = collection_id


class ConfigurationError(CalendaringError):
    """Bad configuration, or a caller-owned session the backend cannot use."""


class AuthenticationError(CalendaringError):
    """401, a bad token."""


class AuthorizationError(AuthenticationError):
    """403."""


class NotFoundError(CalendaringError, LookupError):
    """No such item or collection."""


class AmbiguousError(CalendaringError):
    """A name matched several collections."""


class ConflictError(CalendaringError):
    """A precondition failed; raised as itself when the item changed since it was read."""


class AlreadyExistsError(ConflictError):
    """``add()`` clashed with a stored object.

    ``taken`` is ``"native_id"`` when the name was taken, ``"uid"`` when
    another object holds the UID.
    """

    def __init__(
        self,
        message: str = "",
        *,
        taken: Literal["uid", "native_id"],
        backend: str | None = None,
        collection_id: str | None = None,
    ) -> None:
        super().__init__(message, backend=backend, collection_id=collection_id)
        self.taken = taken


class SyncTokenError(ConflictError):
    """``changes(token)``: the backend can no longer honour the token; start again with ``changes()``."""


class UnsupportedError(CalendaringError):
    """The collection or backend cannot do this; raised before any I/O.

    ``feature`` is the capability that is missing, or ``None`` for a
    limitation of the library itself rather than of a backend.
    """

    def __init__(
        self,
        feature: Feature | None,
        message: str = "",
        *,
        backend: str | None = None,
        collection_id: str | None = None,
    ) -> None:
        super().__init__(message or f"unsupported: {feature}", backend=backend, collection_id=collection_id)
        self.feature = feature


class LossyWriteError(UnsupportedError):
    """The write would lose data, and the loss policy said no.  Nothing was sent."""

    def __init__(
        self,
        losses: Sequence[Loss],
        message: str = "",
        *,
        backend: str | None = None,
        collection_id: str | None = None,
    ) -> None:
        text = message or "lossy write: " + ", ".join(f"{loss.property}={loss.sent!r}" for loss in losses)
        super().__init__(None, text, backend=backend, collection_id=collection_id)
        self.losses = list(losses)


class InvalidDataError(CalendaringError, ValueError):
    """The backend rejected the data, or it does not parse."""


class PartialResultError(InvalidDataError):
    """A read that skipped stored objects it could not use; ``results`` is what it got from the rest.

    ``results`` is what the call returns otherwise (a list of items, or the
    ``ChangeSet`` for ``changes()``, whose token is good to pass on), and
    ``broken`` the objects left out.  A broken object is not reported as
    deleted.
    """

    def __init__(
        self,
        results: Any,
        broken: Sequence[BrokenObject],
        message: str = "",
        *,
        backend: str | None = None,
        collection_id: str | None = None,
    ) -> None:
        text = message or f"{len(broken)} stored object(s) left out: " + ", ".join(
            f"{b.native_id!r} ({b.error})" for b in broken
        )
        super().__init__(text, backend=backend, collection_id=collection_id)
        self.results = results
        self.broken = list(broken)


class RateLimitError(CalendaringError):
    """The backend asked us to slow down.  Raised, never retried here."""

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: float | None = None,
        backend: str | None = None,
        collection_id: str | None = None,
    ) -> None:
        super().__init__(message, backend=backend, collection_id=collection_id)
        self.retry_after = retry_after


class VerificationError(CalendaringError):
    """``verify=True``: the write happened, but the stored item differs from what was sent."""

    def __init__(
        self,
        differences: Sequence[Loss],
        message: str = "",
        *,
        backend: str | None = None,
        collection_id: str | None = None,
    ) -> None:
        text = message or "stored item differs: " + ", ".join(d.property for d in differences)
        super().__init__(text, backend=backend, collection_id=collection_id)
        self.differences = list(differences)


class BackendError(CalendaringError):
    """Anything else from the backend or the transport."""


class TransportError(BackendError):
    """Network, TLS, timeout.  During a write, the outcome is unknown."""


class CalendaringWarning(UserWarning):
    """The base of this library's warnings."""


class LossyWriteWarning(CalendaringWarning):
    """Emitted by the ``WARN`` loss policy."""
