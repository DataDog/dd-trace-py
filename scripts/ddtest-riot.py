#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "riot>=0.22.0",
# ]
# ///
r"""ddtest <-> riot bridge.

This script is the single piece of venv knowledge that ddtest needs in order to
plan and run tests in dd-trace-py. ddtest (a Go binary) owns test planning and
execution; it shells out to this helper for two things:

  - listing the riot venvs ("hashes") that belong to a suite, and
  - obtaining the environment that activates a given venv so ddtest can run
    ``python -m pytest <files>`` inside it.

It uses ``riot`` as a library (the same API ``riot list``/``riot run`` use) so
venv path computation stays in Python rather than being reimplemented in Go.

The script does NOT depend on ddtest being installed: ``hashes`` and ``venv-env``
only read the riotfile and riot's venv model. The ddtest-driven CI path is
opt-in and only runs where the ddtest binary has been downloaded; local
``riot run`` keeps working unchanged.

Subcommands
-----------

``hashes <pattern>``
    Print one line per matching riot venv instance (deduplicated by short hash),
    tab-separated::

        <short_hash>\t<python_hint>\t<DDTEST_SUITE_PATH>

    ``<pattern>`` is a regex matched against venv names/hashes exactly as
    ``riot list <pattern>`` does, so the suitespec ``pattern`` (or suite name)
    can be passed through verbatim. ``DDTEST_SUITE_PATH`` is read from the
    venv's env (see the riotfile); it is empty when the suite did not set it.

``venv-env <hash>``
    Print ``KEY=value`` lines (one per line, value is everything after the first
    ``=``) describing the environment riot would use to run a command in the
    venv identified by ``<hash>`` (a short-hash prefix, matching riot's own
    identifier resolution). The environment mirrors ``riot run --pass-env``:
    the venv's env vars, riot's ``RIOT_*`` instrumentation vars, ``VIRTUAL_ENV``,
    ``PATH`` (venv bin first), and ``PYTHONPATH`` (venv site-packages). It is
    intended to be consumed verbatim by ddtest as the env for
    ``python -m pytest <files>``.

``prepare <hash>``
    Install the per-hash deps (envier, pytest, ...) into the prefix venv
    by calling riot's ``VenvInstance.prepare(env, skip_deps=True)`` directly
    (the same library API ``riot run`` calls internally).
    ``skip_deps=True`` skips the ddtrace dev-package reinstall (already in
    the base venv from build_base_venvs). This replaces the dummy
    ``riot run -s <hash> -- true`` hack: same effect (prepare only, no
    command), but via the library API.

Examples
--------
    scripts/ddtest-riot hashes internal
    scripts/ddtest-riot venv-env 4f70b3c
    scripts/ddtest-riot prepare 4f70b3c
"""

import os
from pathlib import Path
import re
import shlex
import sys


# Env var that carries the suite's test location from the riotfile to ddtest.
SUITE_PATH_ENV = "DDTEST_SUITE_PATH"

# Matches a concrete Python hint like "3.10" (not the bare "3" some venvs use).
_CONCRETE_PY_HINT_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)$")


# ---------------------------------------------------------------------------
# Pure path helpers (no riot, no riotfile) — unit-testable via doctests.
#
# These mirror riot's formulas exactly:
#   - riot.Interpreter.venv_path:
#       <RIOT_ENV_BASE_PATH>/venv_py<major><minor><abiflags>   (abiflags "")
#   - riot.VenvInstance.prefix:
#       <base_venv>_<ident>      (truncated to 255 chars via long_hash)
#   - riot.VenvInstance.site_packages_path:
#       <prefix>/lib/python<major>.<minor><abiflags>/site-packages
# All ancestors of a VenvInstance share the leaf's interpreter, so every
# ancestor's base venv is the leaf's base venv; only ``ident`` differs.
# ---------------------------------------------------------------------------


def py_major_minor(hint):
    """Return ``(major, minor)`` ints from a concrete hint like ``"3.10"``.

    >>> py_major_minor("3.10")
    (3, 10)
    >>> py_major_minor("3.9")
    (3, 9)
    """
    m = _CONCRETE_PY_HINT_RE.match(hint)
    if not m:
        raise ValueError(f"not a concrete Python hint: {hint!r}")
    return int(m.group("major")), int(m.group("minor"))


def base_venv(hint, abiflags="", env_base=None):
    """Return the absolute base venv path for a Python ``hint``.

    Mirrors ``riot.Interpreter.venv_path``. ``env_base`` defaults to
    ``$RIOT_ENV_BASE_PATH`` or ``.riot``.

    >>> base_venv("3.10", env_base="/tmp/riot")
    '/tmp/riot/venv_py310'
    >>> base_venv("3.13", abiflags="t", env_base="/tmp/riot")
    '/tmp/riot/venv_py313t'
    >>> base_venv("3.9", env_base="/custom")
    '/custom/venv_py39'
    """
    major, minor = py_major_minor(hint)
    base = env_base if env_base is not None else os.environ.get("RIOT_ENV_BASE_PATH", ".riot")
    return os.path.abspath(os.path.join(base, f"venv_py{major}{minor}{abiflags}"))


def prefix(ident, long_hash, base):
    """Return the deps install prefix for a venv ``ident``.

    Mirrors ``riot.VenvInstance.prefix``: ``<base>_<ident>``, falling back to
    ``<base>_<long_hash>`` truncated to 255 chars when the ident form is too
    long (riot's own length guard).

    >>> prefix("mock_pytest", "deadbeef", "/tmp/venv_py310")
    '/tmp/venv_py310_mock_pytest'
    >>> long = "a" * 300
    >>> len(prefix("x" * 300, long, "/tmp/venv_py310"))
    255
    """
    prefix_path = "_".join((base, ident))
    if len(prefix_path) > 255:
        prefix_path = "_".join((base, long_hash))[:255]
    return prefix_path


def site_packages(venv, hint, abiflags=""):
    """Return ``<venv>/lib/python<major>.<minor><abiflags>/site-packages``.

    >>> site_packages("/tmp/venv_py310", "3.10")
    '/tmp/venv_py310/lib/python3.10/site-packages'
    """
    major, minor = py_major_minor(hint)
    return os.path.join(venv, "lib", f"python{major}.{minor}{abiflags}", "site-packages")


# ---------------------------------------------------------------------------
# riotfile access (lazy import so the module imports cleanly without riotfile).
# ---------------------------------------------------------------------------


def _load_riotfile():
    """Import and return the riotfile module (added to sys.path on demand)."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "tests"))
    import riotfile  # noqa: E402  — intentional lazy import

    return riotfile


def _matching_instances(pattern):
    """Yield ``(number, instance)`` for venv instances matching ``pattern``.

    Mirrors ``riot list``: the pattern is compiled as a regex and matched
    against the instance name or short hash via ``inst.matches_pattern``.
    Instances without a name are skipped, matching riot's own behavior.
    """
    riotfile = _load_riotfile()
    regex = re.compile(pattern)
    for n, inst in enumerate(riotfile.venv.instances()):
        if not inst.name or not inst.matches_pattern(regex):
            continue
        yield n, inst


def _instance_by_identifier(identifier):
    """Return the VenvInstance whose long hash starts with ``identifier``.

    Matches riot's ``_venvs_matching_identifier`` resolution (a short-hash
    prefix). Exits if no instance matches. riot expands one Venv into several
    instances (per Python version / pkg spec); a short-hash prefix maps to one
    Venv but may yield several instances that share venv path/env, so the first
    is returned.
    """
    riotfile = _load_riotfile()
    identifier = identifier.lstrip("#")
    for _n, inst in enumerate(riotfile.venv.instances()):
        if inst.long_hash.startswith(identifier) or identifier == f"{_n}":
            return inst
    sys.exit(f"No riot venv instance matches '{identifier}'")


# ---------------------------------------------------------------------------
# Instance -> env construction, built on the pure helpers.
# ---------------------------------------------------------------------------


def _inst_abiflags(inst):
    """Return ``inst.py.abiflags()`` or ``""`` when the interpreter is absent.

    abiflags is non-empty only for free-threaded builds (``'t'``); dd-trace-py
    venvs never use free-threaded hints, so the empty default is exact here.
    """
    try:
        return inst.py.abiflags()
    except FileNotFoundError:
        return ""


def _inst_version_str(inst):
    """Return the full Python version for ``RIOT_PYTHON_VERSION``.

    Prefers ``inst.py.version()`` (e.g. ``"3.10.7"``); falls back to the hint
    (``"3.10"``) when the interpreter is not on PATH.
    """
    try:
        return inst.py.version()
    except FileNotFoundError:
        return inst.py._hint


def _inst_base_venv(inst):
    """Return the base venv path for ``inst``.

    Prefers riot's own ``inst.py.venv_path`` (the real path, e.g.
    ``venv_py31020`` for Python 3.10.20 in CI) so the bridge points at the
    same venv riot uses. Falls back to the hint-based ``base_venv()``
    (e.g. ``venv_py310``) only when the interpreter is not probeable
    (local dev without that Python on PATH), since ``venv_path`` shells out
    to the interpreter to get the full version.
    """
    try:
        return os.path.abspath(inst.py.venv_path)
    except FileNotFoundError:
        return base_venv(inst.py._hint, _inst_abiflags(inst))


def _inst_pythonpath(inst):
    """Return riot's PYTHONPATH chain for ``inst`` without building anything.

    Reproduces ``VenvInstance.site_packages_list``: empty entry + cwd, then each
    ancestor venv's site-packages (deps install location, keyed by ``ident``),
    then the base interpreter site-packages. Ancestors share the leaf's
    interpreter, so every ancestor's prefix is ``<leaf_base>_<ancestor_ident>``.
    """
    base = _inst_base_venv(inst)
    hint = inst.py._hint
    abiflags = _inst_abiflags(inst)
    paths = ["", os.getcwd()]
    current = inst
    while current is not None and not current.created:
        if current.pkgs:
            paths.append(site_packages(prefix(current.ident, current.long_hash, base), hint, abiflags))
        current = current.parent
    paths.append(site_packages(base, hint, abiflags))
    return ":".join(paths)


def _inst_scriptpath(inst):
    """Return riot's script PATH for ``inst`` without building anything.

    Reproduces ``VenvInstance.scriptpath``: each ancestor with packages
    contributes ``<prefix>/bin``, then the base interpreter's ``<base>/bin``.
    """
    base = _inst_base_venv(inst)
    paths = []
    current = inst
    while current is not None and not current.created:
        if current.pkgs:
            paths.append(os.path.join(prefix(current.ident, current.long_hash, base), "bin"))
        current = current.parent
    paths.append(os.path.join(base, "bin"))
    return ":".join(paths)


def _venv_env_lines(inst):
    """Build the env dict riot would pass to run_cmd_venv for ``inst``.

    Reproduces ``Session.run`` (with ``pass_env=True``, as the CI invocation
    ``riot run --pass-env`` uses) followed by ``run_cmd_venv``'s own additions
    (VIRTUAL_ENV, PATH, PYTHONPATH), without building or installing anything.
    The venv is assumed to already exist (CI builds it via build_base_venvs).
    """
    # Lazy import so the module imports cleanly without riot installed.
    from riot.runner import ALWAYS_PASS_ENV

    # Start from the current environment (pass_env=True semantics), then layer
    # the venv's own env on top, exactly as Session.run does.
    env = os.environ.copy()
    env.update(dict(inst.env))

    # Riot instrumentation variables, copied verbatim from Session.run.
    env["RIOT"] = "1"
    env["RIOT_PYTHON_HINT"] = str(inst.py)
    env["RIOT_PYTHON_VERSION"] = _inst_version_str(inst)
    env["RIOT_VENV_HASH"] = inst.short_hash
    env["RIOT_VENV_IDENT"] = inst.ident or ""
    env["RIOT_VENV_NAME"] = inst.name or ""
    env["RIOT_VENV_PKGS"] = inst.pkg_str
    env["RIOT_VENV_FULL_PKGS"] = inst.full_pkg_str

    # PYTHONPATH from the instance's site-packages chain (Session.run).
    pythonpath = _inst_pythonpath(inst)
    if pythonpath:
        env["PYTHONPATH"] = f"{pythonpath}:{env['PYTHONPATH']}" if "PYTHONPATH" in env else pythonpath

    # script_path (entry-point scripts) prepended to PATH (Session.run).
    script_path = _inst_scriptpath(inst)
    if script_path:
        env["PATH"] = ":".join((script_path, env.get("PATH", os.environ["PATH"])))

    # run_cmd_venv additions: VIRTUAL_ENV, venv bin on PATH, and the base
    # venv's site-packages appended to PYTHONPATH.
    abs_venv = _inst_base_venv(inst)
    base_site_packages = site_packages(abs_venv, inst.py._hint, _inst_abiflags(inst))
    env["VIRTUAL_ENV"] = abs_venv
    env["PATH"] = f"{abs_venv}/bin:" + env.get("PATH", "")

    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join((existing, base_site_packages)) if existing is not None else base_site_packages

    # ALWAYS_PASS_ENV passthrough from the parent environment (run_cmd_venv).
    for k in ALWAYS_PASS_ENV:
        if k in os.environ and k not in env:
            env[k] = os.environ[k]

    return env


# ---------------------------------------------------------------------------
# Output helpers.
# ---------------------------------------------------------------------------


def _print_env(env):
    # Emit KEY=value lines, shell-quoting the value so the output is safe to
    # `eval` in bash. Riot's RIOT_VENV_FULL_PKGS / _CI_DD_TAGS / SSH_* etc.
    # contain spaces, '<', '~', ':' which break an unquoted eval (e.g.
    # 'hypothesis<6.45.1' is read as a redirection). ddtest parses lines by
    # splitting on the first '=', so a single-quoted value round-trips.
    for key in sorted(env):
        value = env[key]
        if value is None:
            continue
        sys.stdout.write(f"export {key}={shlex.quote(value)}\n")


def cmd_hashes(args):
    seen = set()
    rows = []
    for _n, inst in _matching_instances(args.pattern):
        if inst.short_hash in seen:
            continue
        seen.add(inst.short_hash)
        rows.append((inst.short_hash, str(inst.py._hint), inst.env.get(SUITE_PATH_ENV, "")))
    # Sort by hash to match get-riot-hashes.sh / riot list --hash-only ordering.
    for short_hash, py_hint, suite_path in sorted(rows):
        sys.stdout.write(f"{short_hash}\t{py_hint}\t{suite_path}\n")


def cmd_venv_env(args):
    inst = _instance_by_identifier(args.hash)
    env = _venv_env_lines(inst)
    _print_env(env)


def cmd_prepare(args):
    """Prepare a venv: install per-hash deps into the prefix.

    Calls riot's ``VenvInstance.prepare(env, skip_deps=True)`` directly — the
    same library API ``riot run`` calls internally. ``skip_deps=True`` skips
    the ddtrace dev-package reinstall (already in the base venv from
    build_base_venvs) but installs the per-hash deps (envier, pytest, ...)
    into the prefix venv. This replaces the dummy ``riot run -s <hash> --
    true`` hack: same effect (prepare only, no command), but via the library
    API instead of a no-op shell command.
    """
    inst = _instance_by_identifier(args.hash)
    env = os.environ.copy()
    env.update(dict(inst.env))
    # Surface prepare progress/failures in the CI log. riot logs but some
    # skips are silent; print markers so it's visible, and let exceptions
    # propagate (nonzero exit).
    print(f"Preparing riot venv {inst.short_hash} ({inst.name or 'unnamed'})", flush=True)
    inst.prepare(env, skip_deps=True)
    print(f"Prepared riot venv {inst.short_hash}", flush=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    command, rest = argv[0], argv[1:]

    if command == "hashes":
        if not rest:
            sys.exit("usage: ddtest-riot hashes <pattern>")
        import argparse

        parser = argparse.ArgumentParser(prog="ddtest-riot hashes")
        parser.add_argument("pattern")
        cmd_hashes(parser.parse_args(rest))
        return 0

    if command == "venv-env":
        if not rest:
            sys.exit("usage: ddtest-riot venv-env <hash>")
        import argparse

        parser = argparse.ArgumentParser(prog="ddtest-riot venv-env")
        parser.add_argument("hash")
        cmd_venv_env(parser.parse_args(rest))
        return 0

    if command == "prepare":
        if not rest:
            sys.exit("usage: ddtest-riot prepare <hash>")
        import argparse

        parser = argparse.ArgumentParser(prog="ddtest-riot prepare")
        parser.add_argument("hash")
        cmd_prepare(parser.parse_args(rest))
        return 0

    sys.exit(f"unknown command '{command}'. Use 'hashes', 'venv-env', or 'prepare'.")


if __name__ == "__main__":
    sys.exit(main())
