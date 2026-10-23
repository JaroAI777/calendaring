# Hand-written source; tools/generate_sync.py derives ../_sync/backend.py from this file.
"""The backend ABC (API_DESIGN.md §2.2): one configured store, owning its session if it has one."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar, Self
from urllib.parse import urlsplit

from ..capabilities import Capabilities, Feature, Support
from ..errors import AmbiguousError, CalendaringError, ConfigurationError, NotFoundError, UnsupportedError
from ..loss import LossPolicy
from ..types import Component
from .collection import COMPONENT_FEATURE, AsyncCollection

if TYPE_CHECKING:
    from .workspace import AsyncWorkspace

__all__ = ["AsyncBackend"]

# URL scheme -> backend kind, for open() without kind= (§2.2).  http(s)
# is CalDAV until the "feed or CalDAV" probe of roadmap 2.3, and trackers need kind=.
SCHEMES = {"": "files", "file": "files", "webcal": "feed", "memory": "memory", "http": "caldav", "https": "caldav"}

# Backend kinds that come with an optional extra, and its name.
EXTRAS = {"caldav": "caldav"}


def _none_held(components: frozenset[Component], backend: str, *, left: str | None = None) -> UnsupportedError:
    """The refusal of a collection that would hold none of ``components``, for the first of them.

    ``left`` names one that was made and could not be deleted again.
    """
    wanted = sorted(components, key=str)
    message = f"cannot make a collection holding any of {[str(c) for c in wanted]}"
    if left is not None:
        message += f"; the one made, {left!r}, could not be deleted"
    return UnsupportedError(COMPONENT_FEATURE[wanted[0]], message, backend=backend)


class AsyncBackend(ABC):
    """One configured store: a CalDAV account, a directory, a file, a feed, a tracker instance."""

    kind: ClassVar[str]
    _kinds: ClassVar[dict[str, type[AsyncBackend]]] = {}

    id: str
    capabilities: Capabilities  # the backend-level features: create-collection, delete-collection
    native: object
    loss: LossPolicy | None  # the default for every write; None defers to the workspace, then RAISE
    verify: bool | None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "kind" in cls.__dict__:
            AsyncBackend._kinds[cls.kind] = cls

    def __init__(
        self,
        *,
        id: str,
        capabilities: Capabilities,
        native: object = None,
        loss: LossPolicy | None = None,
        verify: bool | None = None,
    ) -> None:
        self.id = id
        self.capabilities = capabilities
        self.native = native
        self.loss = loss
        self.verify = verify
        self._workspace: AsyncWorkspace | None = None  # set by the workspace holding it; its defaults come last

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}>"

    @classmethod
    async def open(
        cls,
        url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        kind: str | None = None,
        session: Any = None,
        **options: Any,
    ) -> AsyncBackend:
        """The backend for ``url``: the subclass for ``kind``, or for the URL's scheme."""
        if kind is None:
            scheme = urlsplit(url).scheme.lower()
            if scheme not in SCHEMES:
                raise ConfigurationError(f"cannot tell the backend kind from {url!r}; pass kind=")
            kind = SCHEMES[scheme]
        kinds = AsyncBackend._kinds  # not inside the f-string: see tests/test_generated_sync.py
        try:
            klass = kinds[kind]
        except KeyError:
            if kind in EXTRAS:
                extra = EXTRAS[kind]
                raise ConfigurationError(f"the {kind} backend needs: pip install 'calendaring[{extra}]'") from None
            raise ConfigurationError(f"no backend of kind {kind!r} (known: {sorted(kinds)})") from None
        return await klass._open(url, username=username, password=password, token=token, session=session, **options)

    @classmethod
    @abstractmethod
    async def _open(cls, url: str, **options: Any) -> AsyncBackend:
        """Make the backend ``open()`` picked; credentials and ``session`` come in ``options``."""

    # --- collections --------------------------------------------------

    @abstractmethod
    async def collections(self) -> list[AsyncCollection]: ...

    async def collection(self, name_or_id: str) -> AsyncCollection:
        """By id, or else by name; ``NotFoundError``, or ``AmbiguousError`` if several share the name."""
        listed = await self.collections()
        for collection in listed:
            if collection.id == name_or_id:
                return collection
        named = [c for c in listed if c.name == name_or_id]
        if len(named) > 1:
            raise AmbiguousError(f"{len(named)} collections are named {name_or_id!r}", backend=self.id)
        if not named:
            raise NotFoundError(f"no collection {name_or_id!r}", backend=self.id)
        return named[0]

    async def create_collection(
        self, name: str, components: Iterable[Component] = (Component.EVENT,)
    ) -> AsyncCollection:
        if self.capabilities.level(Feature.CREATE_COLLECTION) is Support.UNSUPPORTED:
            raise UnsupportedError(Feature.CREATE_COLLECTION, backend=self.id)
        wanted = frozenset(components)
        made = await self._create_collection(name, wanted)
        if wanted and not made.components & wanted:  # Bedework makes events-only calendars whatever is asked
            if self.capabilities.level(Feature.DELETE_COLLECTION) is Support.UNSUPPORTED:
                raise _none_held(wanted, self.id, left=made.id)
            try:
                await made.delete_collection()
            except CalendaringError as exc:
                raise _none_held(wanted, self.id, left=made.id) from exc
            raise _none_held(wanted, self.id)
        return made

    async def _create_collection(self, name: str, components: frozenset[Component]) -> AsyncCollection:
        """Make the collection; one that holds none of ``components`` is deleted again and refused.

        A backend that knows it cannot hold any of them raises ``_none_held()`` itself, before any I/O.
        """
        raise NotImplementedError(f"{type(self).__name__} declares {Feature.CREATE_COLLECTION} but cannot")

    # --- lifecycle ----------------------------------------------------

    async def close(self) -> None:  # noqa: B027  a hook: most backends have nothing to release
        """Release the session, if the backend owns one."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
