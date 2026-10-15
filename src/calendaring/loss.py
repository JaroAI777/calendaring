"""Loss policies: what a write does when the backend cannot store every value (API_DESIGN.md §5.3).

A policy is called with the list of losses before anything is sent.  If it
returns, the write goes ahead and the backend stores what it can; if it
raises, nothing is sent.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .errors import LossyWriteError, LossyWriteWarning

if TYPE_CHECKING:
    from .items import Item

__all__ = ["ALLOW", "RAISE", "WARN", "Loss", "LossPolicy"]


@dataclass(frozen=True)
class Loss:
    """One value the backend cannot store as given."""

    property: str  # e.g. "PRIORITY", "STATUS", "RELATED-TO"
    sent: object  # the value in the item
    stored: object | None  # what the backend will store instead; None if dropped


class LossPolicy(Protocol):
    def __call__(self, losses: Sequence[Loss], item: Item) -> None: ...


def _raise(losses: Sequence[Loss], item: Item) -> None:
    raise LossyWriteError(losses)


def _warn(losses: Sequence[Loss], item: Item) -> None:
    text = ", ".join(f"{loss.property}={loss.sent!r} -> {loss.stored!r}" for loss in losses)
    warnings.warn(f"lossy write of {item.uid}: {text}", LossyWriteWarning, stacklevel=2)


def _allow(losses: Sequence[Loss], item: Item) -> None:
    return None


RAISE: LossPolicy = _raise
WARN: LossPolicy = _warn
ALLOW: LossPolicy = _allow

# The names a configuration file may use.
BY_NAME: dict[str, LossPolicy] = {"raise": RAISE, "warn": WARN, "allow": ALLOW}
