import pytest

from ddtrace.appsec.iast._metrics import TELEMETRY_DEBUG_VERBOSITY
from ddtrace.appsec.iast._metrics import TELEMETRY_INFORMATION_VERBOSITY
from ddtrace.appsec.iast._metrics import TELEMETRY_MANDATORY_VERBOSITY
from ddtrace.appsec.iast._metrics import metric_verbosity
from tests.utils import override_env


@pytest.mark.parametrize(
    "lvl, env_lvl, expected_result",
    [
        (TELEMETRY_DEBUG_VERBOSITY, "OFF", None),
        (TELEMETRY_MANDATORY_VERBOSITY, "OFF", None),
        (TELEMETRY_INFORMATION_VERBOSITY, "OFF", None),
        (TELEMETRY_DEBUG_VERBOSITY, "DEBUG", 1),
        (TELEMETRY_MANDATORY_VERBOSITY, "DEBUG", 1),
        (TELEMETRY_INFORMATION_VERBOSITY, "DEBUG", 1),
        (TELEMETRY_DEBUG_VERBOSITY, "INFORMATION", None),
        (TELEMETRY_INFORMATION_VERBOSITY, "INFORMATION", 1),
        (TELEMETRY_MANDATORY_VERBOSITY, "INFORMATION", 1),
        (TELEMETRY_DEBUG_VERBOSITY, "MANDATORY", None),
        (TELEMETRY_INFORMATION_VERBOSITY, "MANDATORY", None),
        (TELEMETRY_MANDATORY_VERBOSITY, "MANDATORY", 1),
    ],
)
def test_metric_verbosity(lvl, env_lvl, expected_result):
    with override_env(dict(DD_IAST_TELEMETRY_VERBOSITY=env_lvl)):
        assert metric_verbosity(lvl)(lambda: 1)() == expected_result
