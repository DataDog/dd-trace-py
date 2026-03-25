"""
Bootstrapping code that is run when using the `ddtrace-run` Python entrypoint
Add all monkey-patching that needs to run by default here
"""

#  _____ ___  _________  _____ ______  _____   ___   _   _  _____
# |_   _||  \/  || ___ \|  _  || ___ \|_   _| / _ \ | \ | ||_   _|
#   | |  | .  . || |_/ /| | | || |_/ /  | |  / /_\ \|  \| |  | |
#   | |  | |\/| ||  __/ | | | ||    /   | |  |  _  || . ` |  | |
#  _| |_ | |  | || |    \ \_/ /| |\ \   | |  | | | || |\  |  | |
#  \___/ \_|  |_/\_|     \___/ \_| \_|  \_/  \_| |_/\_| \_/  \_/
# DO NOT MODIFY THIS FILE!
# Only do so if you know what you're doing. This file contains boilerplate code
# to allow injecting a custom sitecustomize.py file into the Python process to
# perform the correct initialisation for the library. All the actual
# initialisation logic should be placed in preload.py.

# AIDEV-NOTE: Apply meson-python editable loader ninja patch *before* importing
# ddtrace.  When ddtrace-run adds ddtrace/bootstrap/ to the head of PYTHONPATH,
# this file is loaded instead of scripts/sitecustomize.py.  Two problems arise:
#
# Problem 1 — missing ninja: pip build isolation creates a temporary overlay env
# with ninja, builds ddtrace, then deletes the overlay.  meson-python records the
# deleted ninja path in _ddtrace_editable_loader.py, so every ddtrace import fails
# with FileNotFoundError.
#
# Problem 2 — missing MesonpyMetaFinder: in riot-managed envs the .pth file that
# registers MesonpyMetaFinder lives in the base venv's site-packages directory.
# Directories added via PYTHONPATH are NOT scanned for .pth files, so the finder
# is never registered when ddtrace-run execs a new Python process.
#
# Fix: if MesonpyMetaFinder is absent, import _ddtrace_editable_loader explicitly
# (its module-level install() call registers the finder).  Then, when ninja is
# missing, replace _rebuild() with a version that reads the already-generated
# meson-info/intro-install_plan.json directly, skipping the subprocess call.
try:

    def _apply_mesonpy_patch():
        import functools
        import json
        import os
        import sys

        # Find MesonpyMetaFinder in sys.meta_path.
        def _get_finder():
            for f in sys.meta_path:
                if hasattr(f, "_build_cmd") and hasattr(f, "_build_path"):
                    return f
            return None

        finder = _get_finder()

        # Fix problem 2: if finder absent, import the loader to register it.
        if finder is None:
            try:
                import _ddtrace_editable_loader  # noqa: F401 — side-effect: calls install()

                finder = _get_finder()
            except ImportError:
                pass

        if finder is None:
            return  # Cannot help without the finder.

        build_cmd = finder._build_cmd
        build_path = finder._build_path

        # Fix problem 1: patch _rebuild() only when ninja binary is missing.
        if not build_cmd or os.path.isfile(str(build_cmd[0])):
            return  # Ninja is present; no patch needed.

        install_plan = os.path.join(build_path, "meson-info", "intro-install_plan.json")
        if not os.path.exists(install_plan):
            return  # Build directory missing; cannot recover.

        try:
            from _ddtrace_editable_loader import collect
        except ImportError:
            return

        @functools.lru_cache(maxsize=1)
        def _safe_rebuild(_p=build_path, _c=collect, _os=os, _json=json):
            _plan = _os.path.join(_p, "meson-info", "intro-install_plan.json")
            with open(_plan, encoding="utf-8") as _f:
                return _c(_json.load(_f))

        finder._rebuild = _safe_rebuild

    _apply_mesonpy_patch()
    del _apply_mesonpy_patch
except Exception:
    pass

import ddtrace  # isort:skip
import os  # noqa:F401
import sys

import ddtrace.bootstrap.cloning as cloning
from ddtrace.internal.logger import get_logger  # noqa:F401
from ddtrace.internal.telemetry import telemetry_writer


log = get_logger(__name__)


try:
    import ddtrace.bootstrap.preload as preload  # Perform the actual initialisation

    cloning.cleanup_loaded_modules()

    # Check for and import any sitecustomize that would have normally been used
    # had ddtrace-run not been used.
    bootstrap_dir = os.path.dirname(__file__)
    if bootstrap_dir in sys.path:
        index = sys.path.index(bootstrap_dir)
        del sys.path[index]

        # Cache this module under its fully qualified package name
        ddtrace_sitecustomize = sys.modules.pop("sitecustomize", None)
        if "ddtrace.bootstrap.sitecustomize" not in sys.modules and ddtrace_sitecustomize is not None:
            sys.modules["ddtrace.bootstrap.sitecustomize"] = ddtrace_sitecustomize

        try:
            import sitecustomize  # noqa:F401
        except ImportError:
            # If an additional sitecustomize is not found then put the ddtrace
            # sitecustomize back.
            log.debug("additional sitecustomize not found")
            if ddtrace_sitecustomize is not None:
                sys.modules["sitecustomize"] = ddtrace_sitecustomize
        else:
            log.debug("additional sitecustomize found in: %s", sys.path)
        finally:
            # Always reinsert the ddtrace bootstrap directory to the path so
            # that introspection and debugging the application makes sense.
            # Note that this does not interfere with imports since a user
            # sitecustomize, if it exists, will be imported.
            sys.path.insert(index, bootstrap_dir)
    else:
        try:
            import sitecustomize  # noqa:F401
        except ImportError:
            log.debug("additional sitecustomize not found")
        else:
            log.debug("additional sitecustomize found in: %s", sys.path)

    if os.getenv("_DD_PY_SSI_INJECT") == "1":
        # _DD_PY_SSI_INJECT is set to `1` in lib-injection/sources/sitecustomize.py when ssi is started
        # and doesn't abort.
        source = "ssi"
    elif os.path.join(os.path.dirname(ddtrace.__file__), "bootstrap") in sys.path:
        # Checks the python path to see if the bootstrap directory is present, this operation
        # is performed in ddtrace/commands/ddtrace_run.py and is triggered by ddtrace-run
        source = "cmd_line"
    else:
        # If none of the above, then the user must have manually configured ddtrace instrumentation
        # ex: import ddtrace.auto
        source = "manual"
    telemetry_writer.add_configuration("instrumentation_source", source, "code")
    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True

    for f in preload.post_preload:
        f()

except Exception:
    loaded = False
    log.warning("error configuring Datadog tracing", exc_info=True)
