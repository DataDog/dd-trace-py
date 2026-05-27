r"""Pytest wrapper around scripts/code_cache_microbench.py.

Skipped by default so the bench never runs in CI. To run locally,
temporarily remove the ``@pytest.mark.skip`` decorator and invoke:

    scripts/run-tests --venv <hash> -- -s -- \
        -k test_code_cache_microbench \
        tests/profiling/collector/test_memalloc_code_cache_bench.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.skip(reason="bench only — run by removing this decorator, see file docstring")
def test_code_cache_microbench() -> None:
    """Loads scripts/code_cache_microbench.py and runs main()."""
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "code_cache_microbench.py"
    assert script_path.exists(), f"bench script not found at {script_path}"
    spec = importlib.util.spec_from_file_location("code_cache_microbench", str(script_path))
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    bench.main()
