# Hand-written source; tools/generate_sync.py derives ../_sync/ from this file.
"""``Workspace.from_config()`` (roadmap 1.4): the shared file, read by calendaring-config."""

from __future__ import annotations

import json
import os
from pathlib import Path

import calendaring_config.discovery
import pytest

from calendaring import AsyncBackend, AsyncWorkspace, ConfigurationError
from calendaring.memory import AsyncMemoryBackend


@pytest.fixture(autouse=True)
def no_outside_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.startswith(("CALDAV_", "JMAP_", "CALENDARING_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(calendaring_config.discovery, "SYSTEM_PATHS", ())


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


async def test_one_backend_per_section_named_after_it(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "home": {"url": "memory:"},
            "work": {"kind": "memory", "url": "memory:"},
            "both": {"contains": ["home", "work"]},
        },
    )
    workspace = await AsyncWorkspace.from_config(path, section="both")
    assert [b.id for b in workspace.backends] == ["home", "work"]
    assert all(isinstance(b, AsyncMemoryBackend) for b in workspace.backends)
    assert all(b._workspace is workspace for b in workspace.backends)


async def test_section_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, {"default": {"url": "memory:"}, "other": {"url": "memory:"}})
    monkeypatch.setenv("CALENDARING_CONFIG_SECTION", "other")
    workspace = await AsyncWorkspace.from_config(path)
    assert [b.id for b in workspace.backends] == ["other"]


async def test_bad_file_is_a_configuration_error(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": {"inherits": "default"}})
    with pytest.raises(ConfigurationError) as info:
        await AsyncWorkspace.from_config(path)
    assert isinstance(info.value.__cause__, calendaring_config.ConfigError)


async def test_no_configuration_is_an_error() -> None:
    with pytest.raises(ConfigurationError, match="no configuration"):
        await AsyncWorkspace.from_config()


async def test_section_without_url_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": {"kind": "memory"}})
    with pytest.raises(ConfigurationError, match="default"):
        await AsyncWorkspace.from_config(path)


async def test_explicit_id_is_kept(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": {"url": "memory:", "id": "mine"}})
    workspace = await AsyncWorkspace.from_config(path)
    assert [b.id for b in workspace.backends] == ["mine"]


async def test_duplicate_ids_are_an_error(tmp_path: Path) -> None:
    path = write(
        tmp_path, {"a": {"url": "memory:", "id": "b"}, "b": {"url": "memory:"}, "both": {"contains": ["a", "b"]}}
    )
    with pytest.raises(ConfigurationError, match="id b"):
        await AsyncWorkspace.from_config(path, section="both")


async def test_key_the_backend_does_not_take_names_the_section(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": {"url": "memory:", "priority": 3}})
    with pytest.raises(ConfigurationError, match="section default"):
        await AsyncWorkspace.from_config(path)


async def test_url_must_be_a_string(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": {"url": 5}})
    with pytest.raises(ConfigurationError, match="url"):
        await AsyncWorkspace.from_config(path)


async def test_error_does_not_repeat_credentials_in_the_url(tmp_path: Path) -> None:
    path = write(tmp_path, {"default": {"url": "https://alice:s3cret@dav.example.com/"}})
    with pytest.raises(ConfigurationError, match="section default") as info:
        await AsyncWorkspace.from_config(path)
    assert "s3cret" not in str(info.value)
    assert "kind" in str(info.value)


async def test_failed_section_closes_the_backends_before_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str] = []

    async def close(self: AsyncMemoryBackend) -> None:
        closed.append(self.id)

    monkeypatch.setattr(AsyncMemoryBackend, "close", close)
    path = write(
        tmp_path, {"a": {"url": "memory:"}, "b": {"url": "memory:", "kind": "no-such"}, "ab": {"contains": ["a", "b"]}}
    )
    with pytest.raises(ConfigurationError, match="section b"):
        await AsyncWorkspace.from_config(path, section="ab")
    assert closed == ["a"]


async def test_file_without_default_section(tmp_path: Path) -> None:
    path = write(tmp_path, {"work": {"url": "memory:"}})
    with pytest.raises(ConfigurationError, match="no default section"):
        await AsyncWorkspace.from_config(path)


async def test_missing_member_warns(tmp_path: Path) -> None:
    path = write(tmp_path, {"a": {"url": "memory:"}, "default": {"contains": ["a", "nosuch"]}})
    with pytest.warns(UserWarning, match="nosuch"):
        workspace = await AsyncWorkspace.from_config(path)
    assert [b.id for b in workspace.backends] == ["a"]


async def test_environment_connections_get_one_id_per_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(AsyncBackend._kinds, "caldav", AsyncMemoryBackend)
    monkeypatch.setitem(AsyncBackend._kinds, "jmap", AsyncMemoryBackend)
    monkeypatch.setenv("CALDAV_URL", "memory:")
    monkeypatch.setenv("JMAP_URL", "memory:")
    workspace = await AsyncWorkspace.from_config()
    assert [b.id for b in workspace.backends] == ["environment-caldav", "environment-jmap"]
