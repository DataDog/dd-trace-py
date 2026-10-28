from unittest import mock

import pytest

from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.standalone import standalone_config


# Each product that independently enables standalone mode, as (config object, attribute, enabled value).
STANDALONE_PRODUCTS = [
    (asm_config, "_asm_enabled", True),
    (asm_config, "_iast_enabled", True),
    (appsec_telemetry_config, "SCA_ENABLED", True),
    (aiguard_config, "_ai_guard_enabled", True),
]


@pytest.mark.parametrize("config,attribute,enabled", STANDALONE_PRODUCTS)
@pytest.mark.parametrize("apm_tracing_enabled", [True, False])
def test_apm_opt_out_per_product(config, attribute, enabled, apm_tracing_enabled):
    """Any single product enabled with APM tracing off opts out; with APM tracing on it does not."""
    with (
        mock.patch.object(config, attribute, enabled),
        mock.patch.object(standalone_config, "apm_tracing_enabled", apm_tracing_enabled),
    ):
        assert standalone_config.apm_opt_out is not apm_tracing_enabled


@pytest.mark.parametrize("apm_tracing_enabled", [True, False])
def test_apm_opt_out_requires_a_product(apm_tracing_enabled):
    """Turning APM tracing off is not enough on its own: no product means no standalone mode."""
    with (
        mock.patch.object(asm_config, "_asm_enabled", False),
        mock.patch.object(asm_config, "_iast_enabled", False),
        mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", None),
        mock.patch.object(aiguard_config, "_ai_guard_enabled", False),
        mock.patch.object(standalone_config, "apm_tracing_enabled", apm_tracing_enabled),
    ):
        assert standalone_config.apm_opt_out is False


def test_sca_only_opts_out_when_explicitly_true():
    """SCA_ENABLED is a tri-state: unset (None) must not opt out of APM."""
    # Pin the other products off: this suite shares a process and _asm_enabled leaks across tests.
    with (
        mock.patch.object(asm_config, "_asm_enabled", False),
        mock.patch.object(asm_config, "_iast_enabled", False),
        mock.patch.object(aiguard_config, "_ai_guard_enabled", False),
        mock.patch.object(standalone_config, "apm_tracing_enabled", False),
    ):
        with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", None):
            assert standalone_config.apm_opt_out is False
        with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", False):
            assert standalone_config.apm_opt_out is False
