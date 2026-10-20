"""The CalDAV backend's capability table follows caldav's hints as API_DESIGN.md §5.1 says (roadmap 2.1)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("caldav")

from caldav.compatibility_hints import FeatureSet  # noqa: E402

from calendaring._caldav_features import backend_capabilities, collection_capabilities  # noqa: E402
from calendaring.capabilities import Feature, Support  # noqa: E402
from calendaring.types import Component  # noqa: E402

ALL = frozenset(Component)


def features(table: dict[str, Any] | None = None) -> Any:
    return FeatureSet(table)  # type: ignore[no-untyped-call]  # caldav leaves it unannotated


def hinted(**levels: str) -> Any:
    """A server caldav has hints for: ``sync_token="unsupported"`` sets ``sync-token``."""
    return features({name.replace("_", "-"): {"support": level} for name, level in levels.items()})


def test_an_unhinted_server_is_unknown_not_unsupported() -> None:
    table = collection_capabilities(features(), ALL)
    assert table.level(Feature.RECURRENCE) is Support.UNKNOWN
    assert table.level(Feature.COMPONENT_TASK) is Support.UNKNOWN
    assert backend_capabilities(features()).level(Feature.CREATE_COLLECTION) is Support.UNKNOWN


def test_what_the_server_stores_is_unknown_without_hints() -> None:
    unhinted = collection_capabilities(features(), ALL)
    known = collection_capabilities(hinted(save_load="full"), ALL)
    for feature in (
        Feature.PROPERTIES_PASSTHROUGH,
        Feature.CATEGORIES,
        Feature.WRITE_CONDITIONAL,
        Feature.TASK_DUE,
        Feature.ALARMS,
        Feature.DATES,
        Feature.TIMEZONES,
        Feature.TIME_LOG,
    ):
        assert unhinted.level(feature) is Support.UNKNOWN, feature
        assert known.level(feature) is Support.FULL, feature


def test_what_the_backend_does_itself_is_declared_whatever_the_server() -> None:
    table = collection_capabilities(features(), ALL)
    assert table.level(Feature.MOVE) is Support.EMULATED
    assert table.level(Feature.CHANGES_KNOWN) is Support.FULL
    assert table.details(Feature.CHANGES) == {"method": "etag-listing"}
    assert table.details(Feature.SEARCH_TIME_RANGE) == {"components": []}


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("full", Support.FULL),
        ("quirk", Support.FULL),
        ("unsupported", Support.UNSUPPORTED),
        ("broken", Support.UNSUPPORTED),
        ("ungraceful", Support.UNSUPPORTED),
        ("fragile", Support.UNKNOWN),
    ],
)
def test_levels_map_as_section_5_1_says(level: str, expected: Support) -> None:
    features = hinted(**{"create-calendar": level, "save-load": "full"})
    assert backend_capabilities(features).level(Feature.CREATE_COLLECTION) is expected


def test_time_zones_follow_the_servers_hint() -> None:
    server = features({"save-load": {"support": "full"}, "save-load.event.timezone": {"support": "unsupported"}})
    assert collection_capabilities(server, ALL).level(Feature.TIMEZONES) is Support.UNSUPPORTED


def test_a_calendar_without_a_component_cannot_hold_it() -> None:
    table = collection_capabilities(hinted(save_load="full"), frozenset({Component.TASK}))
    assert table.level(Feature.COMPONENT_TASK) is Support.FULL
    assert table.level(Feature.COMPONENT_EVENT) is Support.UNSUPPORTED


def test_a_calendar_without_events_has_nowhere_to_log_time() -> None:
    tasks = collection_capabilities(hinted(save_load="full"), frozenset({Component.TASK}))
    assert tasks.level(Feature.TIME_LOG) is Support.UNSUPPORTED
    assert "time_log_collection" in (tasks[Feature.TIME_LOG].note or "")
    both = collection_capabilities(hinted(save_load="full"), frozenset({Component.TASK, Component.EVENT}))
    assert both.level(Feature.TIME_LOG) is Support.FULL


def test_sync_token_is_used_only_where_deletions_are_reported_too() -> None:
    assert collection_capabilities(hinted(sync_token="full"), ALL).details(Feature.CHANGES) == {"method": "sync-token"}
    broken_delete = hinted(**{"sync-token": "full", "sync-token.delete": "unsupported"})
    assert collection_capabilities(broken_delete, ALL).details(Feature.CHANGES) == {"method": "etag-listing"}


def test_sync_token_is_not_used_where_an_expired_token_is_answered_with_a_listing() -> None:
    # Stalwart, OX: 207 and everything for a token they never issued, so deletions would be lost.
    broken = hinted(**{"sync-token": "full", "sync-token.invalid-token": "broken"})
    assert collection_capabilities(broken, ALL).details(Feature.CHANGES) == {"method": "etag-listing"}


def test_time_range_narrows_events_only_where_recurrences_match_too() -> None:
    assert collection_capabilities(hinted(search="full"), ALL).details(Feature.SEARCH_TIME_RANGE) == {
        "components": ["VEVENT"]
    }
    no_implicit = hinted(**{"search": "full", "search.recurrences.includes-implicit.event": "unsupported"})
    assert collection_capabilities(no_implicit, ALL).details(Feature.SEARCH_TIME_RANGE) == {"components": []}
