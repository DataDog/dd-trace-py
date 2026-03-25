"""sitecustomize.py — patch meson-python editable loader for CI/Docker environments.

This file is loaded by Python's site module (via PYTHONPATH) *after* .pth files
in site-packages are processed — which means the meson-python editable meta-path
finder is already registered in sys.meta_path when this runs — but *before* any
user code (pytest, application code) starts importing modules.

Problem 1 — missing ninja: when "pip install -e ." runs with build isolation (the
default), pip creates a temporary overlay environment containing ninja, builds
ddtrace, and then deletes the overlay.  meson-python records the absolute path to
that overlay's ninja binary inside _ddtrace_editable_loader.py.  After the overlay
is gone every import of a ddtrace module causes:

    FileNotFoundError: [Errno 2] No such file or directory:
        '/tmp/pip-build-env-xxx/overlay/bin/ninja'

Problem 2 — missing editable loader in riot prefix venvs: riot installs ddtrace
editable into the base venv (venv_py3140), but runs pytest via PYTHONPATH pointing
only at the prefix venv's site-packages (venv_py3140_<hash>).  The .pth file that
registers the EditableFinder lives in the base venv's site-packages, which is never
processed, so sys.meta_path never contains the EditableFinder.

Problem 3 — subprocess in sys.modules at startup: the generated
_ddtrace_editable_loader.py imports subprocess at module level (it is only used
inside _rebuild(), which we patch away in fix 1).  Because ddtrace-editable.pth
executes "import _ddtrace_editable_loader" before sitecustomize runs, subprocess
appears in sys.modules at Python startup.  This breaks test_auto, which asserts
that "import ddtrace.auto" does not transitively import subprocess.

Fix for problem 2: detect when the EditableFinder is absent, find
_ddtrace_editable_loader.py in the nearest riot base venv, import it properly so it
registers the EditableFinder in sys.meta_path.

Fix for problem 1: after the finder is registered, detect when the stored build
command is unreachable and replace _rebuild() with a version that skips the
subprocess call and reads the already-generated meson-info/intro-install_plan.json
directly.  The plan file is always present because meson-python generated it during
"pip install -e .".

Fix for problem 3: after patching _rebuild() (so subprocess is no longer needed by
the editable loader), remove subprocess from sys.modules if it was imported solely
by the editable loader.  Any code that genuinely needs subprocess can re-import it.

All patches are no-ops in normal dev environments where everything is available.
"""

import functools
import glob
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


_BASE_RIOT_VENV_RE = re.compile(r"^venv_py\d+$")


def _editable_loader_sort_key(match):
    """Prefer loaders for the active interpreter, then base venvs, then shorter paths."""
    path = Path(match)
    venv_name = path.parents[3].name if len(path.parents) > 3 else ""
    py_dir = path.parents[1].name if len(path.parents) > 1 else ""
    current_py_dir = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    is_current_interpreter = py_dir == current_py_dir
    is_base_venv = _BASE_RIOT_VENV_RE.match(venv_name) is not None

    return (
        0 if is_current_interpreter and is_base_venv else 1 if is_current_interpreter else 2 if is_base_venv else 3,
        len(match),
        match,
    )


def _find_ddtrace_editable_loader():
    """Find _ddtrace_editable_loader.py in the project's riot base venvs."""
    # Derive project root: PYTHONPATH includes <project_root>/scripts
    project_root = None
    for path_entry in sys.path:
        candidate = path_entry  # e.g. /home/bits/project/scripts
        if os.path.basename(candidate) == "scripts":
            parent = os.path.dirname(candidate)
            if os.path.isdir(os.path.join(parent, ".riot")):
                project_root = parent
                break
        # Also handle the case where project root is directly in sys.path
        if os.path.isdir(os.path.join(candidate, ".riot")):
            project_root = candidate
            break

    if project_root is None:
        return None

    riot_dir = os.path.join(project_root, ".riot")
    # Search for _ddtrace_editable_loader.py in venv_py* venvs.
    # Prefer shorter names (base venvs: venv_py3140) over longer (prefix: venv_py3140_abc123).
    matches = glob.glob(
        os.path.join(riot_dir, "venv_py*", "lib", "python*", "site-packages", "_ddtrace_editable_loader.py")
    )
    if not matches:
        return None
    # AIDEV-NOTE: A riot checkout can have multiple base venvs alive at once
    # (for example 3.11 and 3.14). Registering the wrong editable loader makes
    # MesonpyMetaFinder expose extension suffixes for the wrong Python version,
    # so imports like ddtrace.internal._threads fail with ModuleNotFoundError.
    return min(matches, key=_editable_loader_sort_key)


def _activate_ddtrace_editable_loader():
    """Load _ddtrace_editable_loader if not already active in sys.meta_path.

    Needed when riot's pytest runs in a venv whose site-packages don't include
    the ddtrace editable install (because it was installed in the base venv).
    """
    # Already active?
    for finder in sys.meta_path:
        if hasattr(finder, "_build_cmd") and hasattr(finder, "_build_path"):
            return  # EditableFinder already registered

    loader_path = _find_ddtrace_editable_loader()
    if loader_path is None:
        return

    try:
        spec = importlib.util.spec_from_file_location("_ddtrace_editable_loader", loader_path)
        mod = importlib.util.module_from_spec(spec)
        # Register in sys.modules so 'from _ddtrace_editable_loader import collect' works.
        sys.modules["_ddtrace_editable_loader"] = mod
        spec.loader.exec_module(mod)
    except Exception:
        # If loading fails, clean up and let errors surface naturally.
        sys.modules.pop("_ddtrace_editable_loader", None)


def _patch_mesonpy_editable():
    """Patch meson-python's editable meta-path finder to survive a missing ninja.

    This is safe to call multiple times; the early-exit guards make it idempotent.
    """
    for finder in sys.meta_path:
        # Identify meson-python's EditableFinder by its characteristic attributes.
        if not (hasattr(finder, "_build_cmd") and hasattr(finder, "_build_path")):
            continue

        build_path = finder._build_path
        build_cmd = finder._build_cmd

        # If the ninja binary exists, no patch is needed.
        if build_cmd and os.path.isfile(str(build_cmd[0])):
            return

        # ninja is missing — patch _rebuild() to skip the subprocess and fall
        # back to reading the existing meson-info/intro-install_plan.json.
        try:
            # collect() is a module-level helper that turns the install plan
            # JSON into meson-python's internal Node tree.
            from _ddtrace_editable_loader import collect as _collect
        except ImportError:
            # If we can't import collect, we can't patch.  Let the original
            # error surface naturally.
            return

        @functools.lru_cache(maxsize=1)
        def _safe_rebuild(_build_path=build_path, _collect=_collect):
            install_plan_path = os.path.join(_build_path, "meson-info", "intro-install_plan.json")
            if not os.path.exists(install_plan_path):
                raise ImportError(
                    f"meson-python: build directory '{_build_path}' has no "
                    "intro-install_plan.json.  The package was not built yet.  "
                    "Run 'pip install -e .' to build it."
                )
            with open(install_plan_path, "r", encoding="utf-8") as f:
                install_plan = json.load(f)
            return _collect(install_plan)

        # Patch only this specific instance so we don't affect other finders.
        finder._rebuild = _safe_rebuild
        return  # Only one ddtrace editable finder expected; done.


def _remove_editable_loader_subprocess():
    """Remove subprocess from sys.modules if it was imported only by _ddtrace_editable_loader.

    The generated _ddtrace_editable_loader.py imports subprocess at module level,
    even though subprocess is only used inside _rebuild() — which we have already
    patched to not call subprocess.  Removing subprocess from sys.modules here
    ensures it does not appear as an implicit import of ddtrace.auto, allowing
    test_auto's assertion to pass.

    We only remove subprocess when:
    1. _ddtrace_editable_loader is in sys.modules (it brought subprocess in), AND
    2. subprocess is currently in sys.modules, AND
    3. The editable loader module holds a direct reference to the subprocess object
       that matches sys.modules["subprocess"] (confirming the loader imported it).

    Any code that genuinely needs subprocess can import it normally afterwards.
    """
    loader_mod = sys.modules.get("_ddtrace_editable_loader")
    if loader_mod is None:
        return

    sub_mod = sys.modules.get("subprocess")
    if sub_mod is None:
        return  # Already removed or never imported

    # Check that the loader module is the one that holds a reference to subprocess
    # (i.e., subprocess was imported as a module-level import in _ddtrace_editable_loader.py).
    loader_subprocess = getattr(loader_mod, "subprocess", None)
    if loader_subprocess is not sub_mod:
        return  # subprocess came from somewhere else — don't touch it

    # Safe to remove: the editable loader is the sole reason subprocess is present.
    del sys.modules["subprocess"]


def _chain_to_next_sitecustomize():
    """Execute the next sitecustomize.py found in sys.path after scripts/.

    When scripts/sitecustomize.py is loaded first (because scripts/ is earlier
    in PYTHONPATH than another directory that has its own sitecustomize.py),
    that other sitecustomize.py is never loaded.  This function finds and execs
    it so that both sets of startup logic run.

    A typical case is tests/internal/sitecustomize.py, which registers a
    post-run module hook that certain subprocess tests rely on.
    """
    this_dir = os.path.dirname(os.path.abspath(__file__))
    for path in sys.path:
        abs_path = os.path.abspath(path) if path else os.getcwd()
        if abs_path == this_dir:
            continue
        candidate = os.path.join(abs_path, "sitecustomize.py")
        if os.path.isfile(candidate):
            try:
                spec = importlib.util.spec_from_file_location("_ddtrace_chained_sitecustomize", candidate)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception:
                pass
            break  # Only chain to the first one found.


_activate_ddtrace_editable_loader()
_patch_mesonpy_editable()
_remove_editable_loader_subprocess()
_chain_to_next_sitecustomize()
