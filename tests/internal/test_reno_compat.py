import importlib.util
from pathlib import Path
from types import SimpleNamespace


spec = importlib.util.spec_from_file_location("reno_compat", Path(__file__).parents[2] / "scripts" / "reno_compat.py")
reno_compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reno_compat)


def change(kind, path):
    return SimpleNamespace(type=kind, new=SimpleNamespace(path=path))


def entry(parents):
    return SimpleNamespace(commit=SimpleNamespace(parents=parents))


def test_merge_note_added_and_modified_uses_addition():
    added = change("add", b"note-123.yaml")
    modified = change("modify", b"note-123.yaml")
    unrelated = change("modify", b"other-456.yaml")
    assert reno_compat.normalize_merge_changes(entry([b"a", b"b"]), [[modified, added], unrelated]) == [
        added,
        unrelated,
    ]


def test_same_uid_different_paths_remain_for_reno_validation():
    changes = [change("add", b"first-123.yaml"), change("modify", b"second-123.yaml")]
    assert reno_compat.normalize_merge_changes(entry([b"a", b"b"]), changes) == changes


def test_nonmerge_entries_unchanged():
    changes = [change("add", b"note-123.yaml"), change("modify", b"note-123.yaml")]
    assert reno_compat.normalize_merge_changes(entry([b"a"]), changes) == changes


def test_delete_and_duplicate_add_entries_remain_for_reno_validation():
    changes = [change("add", b"note-123.yaml"), change("add", b"note-123.yaml"), change("delete", b"note-123.yaml")]
    assert reno_compat.normalize_merge_changes(entry([b"a", b"b"]), [changes]) == changes
