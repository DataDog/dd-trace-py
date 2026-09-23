"""Unit tests for setup.py's up-to-date artifact lookup used by the ext cache."""

from __future__ import annotations

from pathlib import Path
import typing as t
from typing import Any
from typing import Optional


try:
    # Mirror setup.py's own import so the test exercises the same implementation.
    from distutils.dep_util import newer_group
except ImportError:
    from setuptools.modified import newer_group


_SETUP_PATH = Path(__file__).resolve().parents[2] / "setup.py"


def _first_up_to_date_source() -> str:
    """Slice the `_first_up_to_date` helper out of setup.py."""
    source = _SETUP_PATH.read_text()
    start = source.index("def _first_up_to_date(")
    end = source.index("\nclass ", start)
    return source[start:end]


def _exec_helper() -> Any:
    namespace: dict[str, Any] = {"Path": Path, "newer_group": newer_group, "t": t}
    exec(compile(_first_up_to_date_source(), str(_SETUP_PATH), "exec"), namespace)
    return namespace["_first_up_to_date"]


def _touch(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    import os

    os.utime(path, (mtime, mtime))
    return path


def test_returns_none_when_no_candidate_exists(tmp_path: Path) -> None:
    first_up_to_date = _exec_helper()
    source = _touch(tmp_path / "ext.pyx", 1000.0)

    result: Optional[Path] = first_up_to_date(
        [tmp_path / "inplace" / "ext.so", tmp_path / "build" / "ext.so"], [str(source)]
    )

    assert result is None


def test_finds_artifact_in_source_tree(tmp_path: Path) -> None:
    first_up_to_date = _exec_helper()
    source = _touch(tmp_path / "ext.pyx", 1000.0)
    inplace = _touch(tmp_path / "inplace" / "ext.so", 2000.0)

    result = first_up_to_date([inplace, tmp_path / "build" / "ext.so"], [str(source)])

    assert result == inplace


def test_finds_artifact_in_build_lib_when_source_tree_is_empty(tmp_path: Path) -> None:
    """The wheel build path: ext_cache restores under build/lib, not the source tree."""
    first_up_to_date = _exec_helper()
    source = _touch(tmp_path / "ext.pyx", 1000.0)
    build_lib = _touch(tmp_path / "build" / "ext.so", 2000.0)

    result = first_up_to_date([tmp_path / "inplace" / "ext.so", build_lib], [str(source)])

    assert result == build_lib


def test_stale_artifact_is_rejected(tmp_path: Path) -> None:
    first_up_to_date = _exec_helper()
    stale = _touch(tmp_path / "build" / "ext.so", 1000.0)
    source = _touch(tmp_path / "ext.pyx", 2000.0)

    result = first_up_to_date([stale], [str(source)])

    assert result is None


def test_stale_candidate_does_not_mask_a_fresh_one(tmp_path: Path) -> None:
    first_up_to_date = _exec_helper()
    stale = _touch(tmp_path / "inplace" / "ext.so", 1000.0)
    source = _touch(tmp_path / "ext.pyx", 2000.0)
    fresh = _touch(tmp_path / "build" / "ext.so", 3000.0)

    result = first_up_to_date([stale, fresh], [str(source)])

    assert result == fresh


def test_missing_source_forces_a_rebuild(tmp_path: Path) -> None:
    first_up_to_date = _exec_helper()
    artifact = _touch(tmp_path / "build" / "ext.so", 2000.0)

    result = first_up_to_date([artifact], [str(tmp_path / "does-not-exist.pyx")])

    assert result is None
