"""Capabilities, loss policies and the error hierarchy (API_DESIGN.md §5, §6)."""

import pytest

from calendaring import (
    AlreadyExistsError,
    Capabilities,
    Capability,
    ConflictError,
    Feature,
    InvalidDataError,
    LossyWriteError,
    LossyWriteWarning,
    NotFoundError,
    Support,
    SyncTokenError,
    Task,
    UnsupportedError,
)
from calendaring.loss import ALLOW, BY_NAME, RAISE, WARN, Loss


@pytest.mark.parametrize(
    ("given", "level"),
    [
        (True, Support.FULL),
        (False, Support.UNSUPPORTED),
        ("lossy", Support.LOSSY),
        (Support.EMULATED, Support.EMULATED),
    ],
)
def test_the_shapes_a_configuration_file_may_use(given: object, level: Support) -> None:
    assert Capability.coerce(given).level is level  # type: ignore[arg-type]


def test_a_dict_carries_details_and_a_note() -> None:
    capability = Capability.coerce({"level": "lossy", "values": [1, 5, 9], "note": "H/M/L"})
    assert capability.level is Support.LOSSY
    assert capability.details == {"values": [1, 5, 9]}
    assert capability.note == "H/M/L"


def test_supports_is_the_boolean_view() -> None:
    table = Capabilities({"task.priority": "lossy", "changes": "emulated", "move": "unknown", "write": True})
    assert table.supports(Feature.WRITE)
    assert table.supports(Feature.CHANGES)
    assert not table.supports(Feature.TASK_PRIORITY)
    assert table.supports(Feature.TASK_PRIORITY, allow_lossy=True)
    assert not table.supports(Feature.MOVE), "UNKNOWN promises nothing"
    with pytest.raises(KeyError):
        table.level(Feature.CATEGORIES)


def test_updated_replaces_entries() -> None:
    table = Capabilities({"write": True}).updated({Feature.WRITE: False})
    assert table.level(Feature.WRITE) is Support.UNSUPPORTED


def test_stock_loss_policies() -> None:
    losses = [Loss("PRIORITY", 3, 1)]
    item = Task.new(summary="x")
    with pytest.raises(LossyWriteError) as info:
        RAISE(losses, item)
    assert info.value.losses == losses
    with pytest.warns(LossyWriteWarning, match="PRIORITY"):
        WARN(losses, item)
    ALLOW(losses, item)
    assert set(BY_NAME) == {"raise", "warn", "allow"}


def test_errors_are_also_the_builtins() -> None:
    assert issubclass(NotFoundError, LookupError)
    assert issubclass(InvalidDataError, ValueError)
    assert issubclass(AlreadyExistsError, ConflictError)
    assert issubclass(SyncTokenError, ConflictError)
    assert issubclass(LossyWriteError, UnsupportedError)
    assert UnsupportedError(Feature.MOVE).feature is Feature.MOVE
