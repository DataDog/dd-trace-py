import json
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.pytest._tia import _document
from ddtrace.testing.internal.tia.selection import Graph
from ddtrace.testing.internal.tia.store import LocalMapStore


SHA = "a" * 40
OTHER_SHA = "b" * 40
HASH = "c" * 64


def document(sha=SHA):
    return _document(Graph(sha, {"test": {"module.py"}}, set(), set(), set()))


def test_finds_nearest_compatible_commit_and_partitions_configurations(tmp_path):
    store = LocalMapStore(tmp_path)
    store.save(HASH, document())
    store.save(HASH, document(OTHER_SHA))
    assert store.load(HASH, [SHA, OTHER_SHA])["base_commit"] == SHA
    assert store.load(HASH, [OTHER_SHA, SHA])["base_commit"] == OTHER_SHA
    assert store.load("d" * 64, [SHA]) is None
    assert store.load(HASH, ["e" * 40]) is None


@pytest.mark.parametrize(
    "contents",
    [
        "{",
        "null",
        "[]",
        "{}",
        json.dumps({**document(), "configuration_hash": "wrong"}),
        json.dumps({**document(OTHER_SHA), "configuration_hash": HASH}),
        json.dumps({**document(), "configuration_hash": HASH, "map": {"version": 999}}),
    ],
)
def test_corrupt_or_incompatible_entries_are_misses(tmp_path, contents):
    store = LocalMapStore(tmp_path)
    store.save(HASH, document())
    (tmp_path / HASH / f"{SHA}.json").write_text(contents)
    assert store.load(HASH, [SHA]) is None


def test_invalid_recent_entry_does_not_hide_compatible_ancestor(tmp_path):
    store = LocalMapStore(tmp_path)
    store.save(HASH, document())
    (tmp_path / HASH / f"{OTHER_SHA}.json").write_text("{")
    assert store.load(HASH, [OTHER_SHA, SHA])["base_commit"] == SHA


def test_failed_publication_preserves_existing_map_and_cleans_temporary_file(tmp_path):
    store = LocalMapStore(tmp_path)
    store.save(HASH, document())
    replacement = document()
    replacement["map"]["failed"] = ["test"]
    with patch("pathlib.Path.replace", side_effect=OSError("publication failed")):
        store.save(HASH, replacement)
    assert store.load(HASH, [SHA])["map"]["failed"] == []
    assert list((tmp_path / HASH).iterdir()) == [tmp_path / HASH / f"{SHA}.json"]


def test_unreadable_cache_is_a_miss(tmp_path):
    with patch("pathlib.Path.read_bytes", side_effect=PermissionError("read only")):
        assert LocalMapStore(tmp_path).load(HASH, [SHA]) is None


def test_missing_git_metadata_disables_local_cache():
    store = LocalMapStore(None)
    store.save(HASH, document())
    assert store.load(HASH, [SHA]) is None


def test_cached_match_preserves_source_commit_outside_lookup_history(tmp_path):
    store = LocalMapStore(tmp_path)
    artifact = {**document(OTHER_SHA), "matched_commit": SHA}
    store.save(HASH, artifact)
    cached = store.load(HASH, [SHA])
    assert cached["matched_commit"] == SHA
    assert cached["base_commit"] == OTHER_SHA


def test_cached_source_must_be_a_commit_sha(tmp_path):
    store = LocalMapStore(tmp_path)
    store.save(HASH, {**document("--invalid"), "matched_commit": SHA})
    assert store.load(HASH, [SHA]) is None
