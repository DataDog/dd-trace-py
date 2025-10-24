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

        # Cache this module under it's fully qualified package name
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
