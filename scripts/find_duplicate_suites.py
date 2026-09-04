#!/usr/bin/env python3
"""Detect suitespec suites that are duplicates, dead, or latently skipped.

The suitespec system maps each suite to a set of test executions via a regex
``pattern`` (default: the suite name). The repo is mid-migration from riot to
uv: most suites still resolve to riot venvs (``riotfile.venv.instances()``),
while the suites in ``UV_TEST_SUITES`` resolve to uv environments declared in
``suitespec.yml`` (see ``tests.suitespec.get_test_environments``). This tool
handles both, mirroring the split in ``scripts/gen_gitlab_config.py``.

Three kinds of structural bugs hide here:

* **Duplicates** — two active suites in the same runner (riot or uv) resolve
  to the exact same set of executions *and* share the same job context
  (``snapshot``, ``services``, ``env``), so a single PR runs the same tests
  twice. They differ only in trigger ``paths``. Suites that share a venv-set
  but differ in snapshot/services/env are NOT duplicates — ``snapshot: true``
  adds the testagent (without which ``@snapshot`` tests fail), and differing
  services/env change what passes — so they're left out of this category.
* **Dead suites** — a suite resolves to no executions at all, so it can never
  emit a job (``gen_gitlab_config`` warns "No riot venvs found" / no uv envs).
  Often a typo'd or colliding pattern, e.g. an unanchored ``urllib`` that was
  meant to target a stdlib integration but silently matches the unrelated
  ``urllib3`` venv — or a ``webbrowser`` pattern with no matching venv.
* **Skipped-but-functional suites** — ``skip: true`` suppresses the job but the
  suite would otherwise run; listed so latent pattern collisions are visible.

Usage::

    python scripts/find_duplicate_suites.py            # report
    python scripts/find_duplicate_suites.py --paths    # also print trigger paths

Dev/diagnostic tool; not wired into CI.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import re
import sys


# Ensure we import THIS worktree's tests.suitespec/riotfile, not whatever an
# ambient PYTHONPATH happens to point at.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import riot  # noqa: F401, E402

import riotfile  # noqa: F401, E402  -- imported for side effects (defines riot.venv)
import tests.suitespec as spec  # noqa: E402


def _suite_pattern(suite: str, config: dict) -> str:
    return config.get("pattern", suite)


def _is_uv_suite(suite: str, config: dict) -> bool:
    """Whether a suite runs under uv (not riot).

    Mirrors ``gen_gitlab_config.collect_all_suite_venv_info``: a UV_TEST_SUITES
    member without ``ddtest`` is skipped from riot matching and run via uv
    environments instead. (A UV_TEST_SUITES member *with* ``ddtest`` still
    matches riot venvs for the ddtest run, so it stays in the riot bucket.)
    """
    return suite in spec.UV_TEST_SUITES and not config.get("ddtest")


def _matching_riot_venvs(suite_regexes: dict[str, re.Pattern]) -> dict[str, frozenset[tuple[str, str]]]:
    """Map each riot suite to the frozenset of (name, short_hash) venvs it matches."""
    matched: dict[str, set[tuple[str, str]]] = {s: set() for s in suite_regexes}
    for inst in riotfile.venv.instances():  # type: ignore[attr-defined]
        if not inst.name:
            continue
        key = (inst.name, inst.short_hash)  # type: ignore[attr-defined]
        for suite, regex in suite_regexes.items():
            if inst.matches_pattern(regex):  # type: ignore[attr-defined]
                matched[suite].add(key)
    return {s: frozenset(v) for s, v in matched.items()}


def _job_context_key(config: dict) -> tuple:
    """Job-level dimensions that change *what runs / what passes*, not just how.

    Two suites matching the same venvs are only true duplicates when they also
    share these: ``snapshot`` (adds the testagent — @snapshot tests fail
    without it), ``services`` (backing services like mysql/httpbin), and ``env``
    (environment variables that alter behavior). ``parallelism``/``retry``/
    ``timeout`` only affect *how* a suite runs, so they're intentionally excluded.
    """
    snapshot = bool(config.get("snapshot", False))
    services = tuple(config.get("services") or [])
    env = tuple(sorted((config.get("env") or {}).items()))
    return (snapshot, services, env)


def _execution_key(execution_set: frozenset, config: dict) -> tuple:
    """Full identity: the matched executions plus their job context."""
    return (execution_set, _job_context_key(config))


def _uv_env_key(env) -> tuple:
    """Hashable identity for a uv TestEnvironment, excluding the suite name.

    Two suites are duplicates when their environments match on variant name,
    Python, dependencies, and commands — the ``suite`` field itself is what
    differs between duplicate suites, so it must be excluded.
    """
    return (env.name, env.python, env.direct_dependencies, tuple(env.runs))


def _matching_uv_envs(uv_suites: dict[str, dict]) -> dict[str, frozenset[tuple]]:
    """Map each uv suite to the frozenset of its environment identities."""
    nightly = False
    try:
        envs = spec.get_test_environments(nightly=nightly)
    except Exception as exc:  # noqa: BLE001
        print(f"# WARNING: failed to load uv test environments: {exc}")
        return {s: frozenset() for s in uv_suites}
    return {s: frozenset(_uv_env_key(e) for e in envs.get(s, ())) for s in uv_suites}


def _print_suite(suite: str, cfg: dict, show_paths: bool) -> None:
    skip = " [skip]" if cfg.get("skip") else ""
    runner = "uv" if _is_uv_suite(suite, cfg) else "riot"
    print(
        f"  - {suite}  (runner={runner}, pattern={_suite_pattern(suite, cfg)!r}, "
        f"parallelism={cfg.get('parallelism')}, snapshot={cfg.get('snapshot', False)}){skip}"
    )
    if show_paths:
        for p in cfg.get("paths", []):
            print(f"      {p}")


def _report_duplicates(label: str, by_key: dict[tuple, list[str]], suites: dict[str, dict], show_paths: bool) -> bool:
    """Print duplicate groups sharing the same non-empty execution identity. Return True if any found."""
    print(f"## Duplicate {label} suites (same venvs + snapshot/services/env)")
    found = False
    for key, group in sorted(by_key.items(), key=lambda kv: (-len(kv[1]), sorted(kv[1]))):
        # key = (execution_set, (snapshot, services, env)); skip empty execution sets
        exec_set = key[0]
        if not exec_set or len(group) < 2:
            continue
        active = [s for s in group if not suites[s].get("skip")]
        if len(active) < 2:
            note = "  (only one active — skipped members would collide if un-skipped)"
        else:
            note = f"  ({len(active)} active — wasted CI)"
        found = True
        print(f"### {len(group)} suites share the same {len(exec_set)} execution(s){note}")
        for suite in sorted(group):
            _print_suite(suite, suites[suite], show_paths)
        print()
    if not found:
        print("(none)\n")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", action="store_true", help="also print each suite's trigger paths")
    args = parser.parse_args()

    suites = spec.get_suites()

    # Benchmark suites (type contains "benchmark") use a separate runner
    # (BenchmarkSpec -> benchmarks/<name>/scenario.py) and do NOT execute the
    # riot venvs / uv environments their pattern happens to match. Excluding
    # them avoids false positives where a benchmark shares a name with a test.
    test_suites = {s: c for s, c in suites.items() if "benchmark" not in c.get("type", "test")}

    riot_suites = {s: c for s, c in test_suites.items() if not _is_uv_suite(s, c)}
    uv_suites = {s: c for s, c in test_suites.items() if _is_uv_suite(s, c)}

    # --- riot: match patterns against riotfile venvs ---
    riot_regexes: dict[str, re.Pattern] = {}
    for suite, config in riot_suites.items():
        pattern = _suite_pattern(suite, config)
        try:
            riot_regexes[suite] = re.compile(pattern)
        except re.error:
            print(f"# WARNING: invalid pattern for suite {suite!r}: {pattern}")
    riot_matched = _matching_riot_venvs(riot_regexes)

    # --- uv: resolve environments declared in suitespec ---
    uv_matched = _matching_uv_envs(uv_suites)

    n_skipped = sum(1 for c in test_suites.values() if c.get("skip"))
    n_benchmarks = len(suites) - len(test_suites)
    print(
        f"# {len(test_suites)} test suites ({n_benchmarks} benchmark suites skipped), "
        f"{n_skipped} skipped, {len(riot_suites)} riot, {len(uv_suites)} uv"
    )
    print()

    # --- Category 1: duplicates, per runner ---
    # Group by the full execution identity (matched executions + job context:
    # snapshot/services/env). Two suites sharing only a venv-set but differing
    # in snapshot/services are NOT duplicates — they run different outcomes.
    riot_by_key: dict[tuple, list[str]] = defaultdict(list)
    for suite, exec_set in riot_matched.items():
        riot_by_key[_execution_key(exec_set, riot_suites[suite])].append(suite)
    dup_riot = _report_duplicates("riot", riot_by_key, riot_suites, args.paths)

    uv_by_key: dict[tuple, list[str]] = defaultdict(list)
    for suite, exec_set in uv_matched.items():
        uv_by_key[_execution_key(exec_set, uv_suites[suite])].append(suite)
    dup_uv = _report_duplicates("uv", uv_by_key, uv_suites, args.paths)

    # --- Category 2: dead suites (no executions in their runner) ---
    dead_riot = sorted(s for s, k in riot_matched.items() if not k)
    dead_uv = sorted(s for s, k in uv_matched.items() if not k)
    print("## Dead suites (resolve to no executions — would emit zero jobs)")
    if dead_riot or dead_uv:
        for suite in dead_riot:
            _print_suite(suite, riot_suites[suite], args.paths)
        for suite in dead_uv:
            _print_suite(suite, uv_suites[suite], args.paths)
        print()
    else:
        print("(none)\n")

    # --- Category 3: skipped suites that would run if un-skipped (latent) ---
    latent_riot = sorted(s for s in riot_matched if riot_suites[s].get("skip") and riot_matched[s])
    latent_uv = sorted(s for s in uv_matched if uv_suites[s].get("skip") and uv_matched[s])
    print("## Skipped suites (latent: would run if un-skipped)")
    if latent_riot or latent_uv:
        for suite in latent_riot:
            cfg = riot_suites[suite]
            print(
                f"  - {suite}  (runner=riot, pattern={_suite_pattern(suite, cfg)!r}, "
                f"matches {len(riot_matched[suite])} venv(s))"
            )
            if args.paths:
                for p in cfg.get("paths", []):
                    print(f"      {p}")
        for suite in latent_uv:
            cfg = uv_suites[suite]
            print(
                f"  - {suite}  (runner=uv, pattern={_suite_pattern(suite, cfg)!r}, "
                f"matches {len(uv_matched[suite])} env(s))"
            )
            if args.paths:
                for p in cfg.get("paths", []):
                    print(f"      {p}")
        print()
    else:
        print("(none)\n")

    # Exit non-zero on real bugs (active duplicates, dead suites) so this can
    # gate CI. Skipped-latent suites are informational only — a skipped suite
    # that *would* run is not a bug until someone un-skips it.
    has_dup = dup_riot or dup_uv
    has_active_dead = any(not riot_suites[s].get("skip") for s in dead_riot) or any(
        not uv_suites[s].get("skip") for s in dead_uv
    )
    return 1 if (has_dup or has_active_dead) else 0


if __name__ == "__main__":
    raise SystemExit(main())
