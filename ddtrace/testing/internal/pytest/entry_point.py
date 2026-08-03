from pathlib import Path

from ddtrace.internal.settings import env
from ddtrace.testing.internal.pytest.xdist import wait_for_xdist_worker_manifest


wait_for_xdist_worker_manifest(Path.cwd())

# NOTE: Changed default value to true
if env.get("DD_PYTEST_USE_NEW_PLUGIN", "").lower() in ("0", "false"):
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403
else:
    from ddtrace.testing.internal.pytest.plugin import *  # noqa: F403
