"""The backend conformance suite, for a backend's own test module (roadmap 1.2).

Needs pytest and pytest-asyncio (``asyncio_mode = "auto"``).  See
``AsyncConformance`` for how to use it.
"""

from ._async.conformance import AsyncConformance
from ._sync.conformance import SyncConformance

__all__ = ["AsyncConformance", "SyncConformance"]
