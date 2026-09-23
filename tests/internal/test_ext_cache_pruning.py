"""Unit tests for the ext cache's generation eviction."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


_EXT_CACHE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ext_cache.py"


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


def test_keeps_every_generation_under_the_limit(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    for digest in ("aaa", "bbb"):
        _make_generation(tmp_path, "ext", digest)

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "bbb")], keep=3)

    assert sorted(d.name for d in (tmp_path / "ext").iterdir()) == ["aaa", "bbb"]


def test_evicts_least_recently_used_beyond_the_limit(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    for digest in ("g1", "g2", "g3"):
        _make_generation(tmp_path, "ext", digest)
        ext_cache._record_and_prune(tmp_path, [_entry("ext", digest)], keep=2)

    assert sorted(d.name for d in (tmp_path / "ext").iterdir()) == ["g2", "g3"]


def test_current_build_is_never_evicted(tmp_path: Path) -> None:
    """A branch that keeps rebuilding the same hash must not lose it to newer generations."""
    ext_cache = _load_ext_cache()
    for digest in ("old", "mid", "new"):
        _make_generation(tmp_path, "ext", digest)
        ext_cache._record_and_prune(tmp_path, [_entry("ext", digest)], keep=3)

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "old")], keep=1)

    assert [d.name for d in (tmp_path / "ext").iterdir()] == ["old"]


def test_eviction_is_per_extension(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    for digest in ("a1", "a2"):
        _make_generation(tmp_path, "one", digest)
        ext_cache._record_and_prune(tmp_path, [_entry("one", digest)], keep=1)
    _make_generation(tmp_path, "two", "b1")
    ext_cache._record_and_prune(tmp_path, [_entry("two", "b1")], keep=1)

    assert [d.name for d in (tmp_path / "one").iterdir()] == ["a2"]
    assert [d.name for d in (tmp_path / "two").iterdir()] == ["b1"]


def test_unrelated_cache_subtrees_are_left_alone(tmp_path: Path) -> None:
    """shared_deps is keyed by config hash, not by extension, and is not ours to evict."""
    ext_cache = _load_ext_cache()
    shared = tmp_path / "shared_deps" / "absl" / "cfg0"
    shared.mkdir(parents=True)
    _make_generation(tmp_path, "ext", "aaa")

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], keep=1)

    assert shared.exists()


def test_a_corrupt_index_does_not_fail_the_build(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    _make_generation(tmp_path, "ext", "aaa")
    (tmp_path / "usage.json").write_text("{not json")

    ext_cache._record_and_prune(tmp_path, [_entry("ext", "aaa")], keep=1)

    assert _index(tmp_path)["entries"] == {"ext/aaa": 1}


def test_index_drops_keys_for_evicted_generations(tmp_path: Path) -> None:
    ext_cache = _load_ext_cache()
    for digest in ("g1", "g2"):
        _make_generation(tmp_path, "ext", digest)
        ext_cache._record_and_prune(tmp_path, [_entry("ext", digest)], keep=1)

    assert _index(tmp_path)["entries"] == {"ext/g2": 2}
