"""Unit tests for the ext cache's age-based eviction."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


_EXT_CACHE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ext_cache.py"
DAY = 86400.0


def _load_ext_cache() -> Any:
    spec = importlib.util.spec_from_file_location("_ext_cache_under_test", _EXT_CACHE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(name: str, digest: str) -> tuple[str, str, str]:
    return (name, digest, f"build/lib/{name}.so")


def _make_generation(cache: Path, name: str, digest: str) -> Path:
    d = cache / name / digest
    d.mkdir(parents=True)
    (d / f"{name}.so").write_bytes(b"")
    return d


def _index(cache: Path) -> dict:
    return json.loads((cache / "usage.json").read_text())


def test_keeps_an_entry_rebuilt_within_the_max_age(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "ext", "aaa")

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], now=1000.0, max_age_days=2)
    ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], now=1000.0 + DAY, max_age_days=2)

    assert [d.name for d in (tmp_path / "ext").iterdir()] == ["aaa"]


def test_evicts_an_entry_untouched_past_the_max_age(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "ext", "old")
    ext_cache._record_and_prune(tmp_path, [_entry("ext", "old")], now=1000.0, max_age_days=2)

    _make_generation(tmp_path, "ext", "new")
    ext_cache._record_and_prune(tmp_path, [_entry("ext", "new")], now=1000.0 + 3 * DAY, max_age_days=2)

    assert [d.name for d in (tmp_path / "ext").iterdir()] == ["new"]


def test_current_build_is_never_evicted(tmp_path: Path) -> None:
    """A branch that keeps rebuilding the same hash must not lose it, no matter how much
    wall-clock time passes between rebuilds.
    """
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "ext", "aaa")
    now = 1000.0
    for _ in range(5):
        ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], now=now, max_age_days=2)
        now += 3 * DAY

    assert [d.name for d in (tmp_path / "ext").iterdir()] == ["aaa"]


def test_a_renamed_extension_ages_out_instead_of_persisting(tmp_path: Path) -> None:
    """Each generation lives under its extension's name. Once that name stops appearing in
    ext_entries, nothing marks the generation as used. The generation must still age out on
    the same sweep, rather than surviving in the archive forever.
    """
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "old_name", "aaa")
    ext_cache._record_and_prune(tmp_path, [_entry("old_name", "aaa")], now=1000.0, max_age_days=2)

    ext_cache._record_and_prune(tmp_path, [_entry("new_name", "bbb")], now=1000.0 + 3 * DAY, max_age_days=2)

    assert not (tmp_path / "old_name").exists() or list((tmp_path / "old_name").iterdir()) == []


def test_unrelated_cache_subtrees_are_left_alone(tmp_path: Path) -> None:
    """shared_deps is keyed by config hash, not by extension, and is not ours to evict."""
    ext_cache = _load_ext_cache()
    shared = tmp_path / "shared_deps" / "absl" / "cfg0"
    shared.mkdir(parents=True)
    _make_generation(tmp_path, "ext", "aaa")

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], now=1000.0 + 30 * DAY, max_age_days=2)

    assert shared.exists()


def test_a_corrupt_index_does_not_fail_the_build(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "ext", "aaa")
    (tmp_path / "usage.json").write_text("{not json")

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], now=1000.0, max_age_days=2)

    assert _index(tmp_path)["entries"] == {"ext/aaa": 1000.0}


def test_index_drops_keys_for_evicted_generations(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "ext", "g1")
    ext_cache._record_and_prune(tmp_path, [_entry("ext", "g1")], now=1000.0, max_age_days=2)

    _make_generation(tmp_path, "ext", "g2")
    ext_cache._record_and_prune(tmp_path, [_entry("ext", "g2")], now=1000.0 + 3 * DAY, max_age_days=2)

    assert _index(tmp_path)["entries"] == {"ext/g2": 1000.0 + 3 * DAY}
