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
import ddtrace.bootstrap.cloning as cloning  # noqa

import os  # noqa:F401
import sys  # noqa:F401

# configure ddtrace logger before other modules log
from ddtrace._logger import configure_ddtrace_logger

configure_ddtrace_logger()  # noqa: E402

# Enable telemetry writer and excepthook as early as possible to ensure we
# capture any exceptions from initialization
import ddtrace.internal.telemetry  # noqa: E402

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace import config  # noqa:F401
from ddtrace._logger import DD_LOG_FORMAT
from ddtrace.internal.logger import get_logger  # noqa:F401

# TODO(mabdinur): Remove this once we have a better way to start the mini agent
from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent as _start_mini_agent

_start_mini_agent()

# Debug mode from the tracer will do the same here, so only need to do this otherwise.
if config._logs_injection:
    from ddtrace import patch

    patch(logging=True)
    ddtrace_logger = logging.getLogger("ddtrace")
    for handler in ddtrace_logger.handlers:
        handler.setFormatter(logging.Formatter(DD_LOG_FORMAT))


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

        # NOTE: this reference to the module is crucial in Python 2.
        # Without it the current module gets gc'd and all subsequent references
        # will be `None`.
        ddtrace_sitecustomize = sys.modules["sitecustomize"]
        del sys.modules["sitecustomize"]

        # Cache this module under it's fully qualified package name
        if "ddtrace.bootstrap.sitecustomize" not in sys.modules:
            sys.modules["ddtrace.bootstrap.sitecustomize"] = ddtrace_sitecustomize

        try:
            import sitecustomize  # noqa:F401
        except ImportError:
            # If an additional sitecustomize is not found then put the ddtrace
            # sitecustomize back.
            log.debug("additional sitecustomize not found")
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

    telemetry_writer.add_configuration("ddtrace_bootstrapped", True, "unknown")
    telemetry_writer.add_configuration("ddtrace_auto_used", "ddtrace.auto" in sys.modules, "unknown")
    # Loading status used in tests to detect if the `sitecustomize` has been
    # properly loaded without exceptions. This must be the last action in the module
    # when the execution ends with a success.
    loaded = True

    for f in preload.post_preload:
        f()

except Exception:
    loaded = False
    log.warning("error configuring Datadog tracing", exc_info=True)
