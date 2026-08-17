import os
import time

import mock
import pytest

from ddtrace.appsec._capabilities import _appsec_rc_capabilities
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.appsec._remoteconfiguration import AppSecCallback
from ddtrace.appsec._remoteconfiguration import disable_appsec_rc
from ddtrace.appsec._remoteconfiguration import enable_appsec_rc
from ddtrace.appsec._utils import get_triggers
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.internal.appsec.product import _disable_asm
from ddtrace.internal.appsec.product import _enable_asm
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.utils.formats import asbool
import tests.appsec.rules as rules
from tests.appsec.utils import asm_context
from tests.appsec.utils import build_payload
from tests.appsec.utils import get_waf_addresses
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.fixture
def appsec_callback():
    return AppSecCallback(_enable_asm, _disable_asm)


@pytest.fixture(autouse=True)
def patch_remoteconfig_poller_for_appsec(rc_poller):
    """Patch the global remoteconfig_poller with the test fixture for appsec tests."""
    with mock.patch("ddtrace.appsec._remoteconfiguration.remoteconfig_poller", rc_poller):
        yield


@pytest.mark.subprocess(env={"DD_APPSEC_RULES": ""})
def test_empty_appsec_rules_is_unset():
    from ddtrace.internal.settings.asm import config

    assert config._asm_static_rule_file is None


def _set_and_get_appsec_tags(tracer, check_client_id=False):
    with asm_context(tracer) as span:
        set_http_meta(
            span,
            {},
            raw_uri="http://example.com/.git",
            status_code="404",
            request_cookies={"cookie1": "im the cookie1"},
        )
    if check_client_id:
        assert span._local_root._get_str_attribute(APPSEC.RC_CLIENT_ID)
    return get_triggers(span)


def test_appsec_callback_uses_injected_lifecycle(rc_poller):
    enable_asm = mock.Mock()
    disable_asm = mock.Mock()
    callback = AppSecCallback(enable_asm, disable_asm)

    with mock.patch("ddtrace.appsec._remoteconfiguration._process_asm_features") as process_asm_features:
        callback([build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")])

    process_asm_features.assert_called_once()
    assert process_asm_features.call_args.kwargs["enable_asm"] is enable_asm
    assert process_asm_features.call_args.kwargs["disable_asm"] is disable_asm


def test_appsec_product_wires_remote_configuration():
    from ddtrace.internal.appsec import product

    with (
        mock.patch.object(product.config, "_asm_enabled", False),
        mock.patch.object(product.config, "_asm_can_be_enabled", False),
        mock.patch.object(product.config, "_asm_rc_enabled", True),
        mock.patch("ddtrace.appsec._remoteconfiguration.enable_appsec_rc") as enable_rc,
    ):
        product.start()

        callback = enable_rc.call_args.args[0]
        assert callback._enable_asm is product._enable_asm
        assert callback._disable_asm is product._disable_asm


@pytest.mark.xfail(
    reason="DD_REMOTE_CONFIGURATION_ENABLED is set to false for all riot venvs, "
    "this is not the default behavior for users"
)
def test_rc_enabled_by_default(tracer):
    # TODO: remove https://github.com/DataDog/dd-trace-py/blob/1.x/riotfile.py#L100 or refactor this test
    result = _set_and_get_appsec_tags(tracer)
    assert result is None
    assert asm_config._asm_can_be_enabled


def test_rc_activate_is_active_and_get_processor_tags(tracer, rc_poller, appsec_callback):
    with override_global_config(dict(_remote_config_enabled=True)):
        rc_config = build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")
        appsec_callback([rc_config])
        assert AppSecSpanProcessor._instance
        assert _set_and_get_appsec_tags(tracer)
        rc_config = build_payload("ASM_FEATURES", None, "config")
        appsec_callback([rc_config])
        result = _set_and_get_appsec_tags(tracer)
        assert result is None
        assert AppSecSpanProcessor._instance is None
        rc_config = build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")
        appsec_callback([rc_config])
        assert AppSecSpanProcessor._instance
        assert _set_and_get_appsec_tags(tracer)


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", True),
        ("true", True),
        ("true", False),
    ],
)
def test_rc_activation_states_on(tracer, appsec_enabled, rc_value, rc_poller, appsec_callback):
    with (
        override_env({APPSEC.ENV: appsec_enabled} if appsec_enabled else {}),
        override_global_config(dict(_asm_enabled=asbool(appsec_enabled), _remote_config_enabled=True)),
    ):
        if appsec_enabled:
            tracer.configure(appsec_enabled=asbool(appsec_enabled))
        rc_config = build_payload("ASM_FEATURES", {"asm": {"enabled": rc_value}}, "config")
        appsec_callback([rc_config])
        result = _set_and_get_appsec_tags(tracer)
        assert result


@pytest.mark.parametrize(
    "appsec_enabled, rc_value",
    [
        ("", False),
        ("false", False),
        ("false", True),
    ],
)
def test_rc_activation_states_off(tracer, appsec_enabled, rc_value, rc_poller, appsec_callback):
    with override_env({APPSEC.ENV: appsec_enabled}):
        if appsec_enabled == "":
            del os.environ[APPSEC.ENV]
        with override_global_config(dict(_asm_enabled=True)):
            tracer.configure(appsec_enabled=asbool(appsec_enabled))

            rc_configs = [build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")]
            if rc_value is False:
                rc_configs = []

            appsec_callback(rc_configs)
            result = _set_and_get_appsec_tags(tracer)
            assert result is None


@pytest.mark.parametrize(
    "rc_enabled, appsec_enabled, capability",
    [
        (True, "true", "DC+B5wP8"),  # All capabilities except ASM_ACTIVATION
        (False, "true", ""),
        (True, "false", "gAAAAA=="),
        (False, "false", ""),
        (True, "", "gAAAAg=="),  # ASM_ACTIVATION
        (False, "", ""),
    ],
)
def test_rc_capabilities(rc_enabled, appsec_enabled, capability, tracer):
    env = {}
    config = {}
    tracer.configure(appsec_enabled=False)
    if appsec_enabled:
        env[APPSEC.ENV] = appsec_enabled
        config["appsec_enabled"] = asbool(appsec_enabled)
    with override_env(env):
        with override_global_config(dict(_remote_config_enabled=rc_enabled)):
            tracer.configure(**config)
            assert _appsec_rc_capabilities() == capability


@pytest.mark.parametrize(
    "env_rules, expected",
    [
        ({}, "DC+B5wP+"),  # All capabilities
        ({"_asm_static_rule_file": DEFAULT.RULES}, "gAAAAg=="),  # Only ASM_FEATURES
    ],
)
def test_rc_activation_capabilities(tracer, rc_poller, env_rules, expected, appsec_callback):
    global_config = dict(_asm_enabled=False, _remote_config_enabled=True)
    global_config.update(env_rules)
    with override_global_config(global_config):
        rc_configs = [build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")]
        # flaky test
        # assert not rc_poller._worker

        appsec_callback(rc_configs)

        assert _appsec_rc_capabilities() == expected


def test_rc_capabilities_updated_after_one_click_activation(tracer, rc_poller, appsec_callback):
    """Regression test: advertised capabilities must follow one-click ASM activation/deactivation.

    Capabilities are registered once (while AppSec is still disabled for a remotely-activated
    service). The client must re-advertise blocking/RASP once ASM is turned on and drop
    them again when it is turned off, otherwise the backend reports "UPDATE REQUIRED" for
    blocking even though it is functional. This exercises the appsec wiring
    (AppSecCallback -> update_capabilities(_ALL_ASM_CAPABILITIES, _rc_capabilities()));
    the replace-within-mask mechanics themselves are covered by the native RC tests.
    """
    from ddtrace.appsec._capabilities import _ALL_ASM_BLOCKING
    from ddtrace.appsec._capabilities import _rc_capabilities
    from ddtrace.internal.native import RemoteConfigCapabilities as Cap

    def advertised():
        # The native adapter buffers the capabilities it advertises to the agent as a
        # list of RemoteConfigCapabilities; a set makes membership assertions order-safe.
        return set(rc_poller._client._capability_values)

    enable_config = [build_payload("ASM_FEATURES", {"asm": {"enabled": True}}, "config")]
    disable_config = [build_payload("ASM_FEATURES", {"asm": {}}, "config")]

    with override_global_config(dict(_remote_config_enabled=True, _asm_enabled=False, _asm_can_be_enabled=True)):
        enable_appsec_rc(appsec_callback)

        # AppSec is not enabled yet (remote activation pending): activation only, no blocking.
        assert Cap.AsmActivation in advertised()
        assert advertised().isdisjoint(_ALL_ASM_BLOCKING)

        # One-click activation: blocking (and RASP) capabilities must now be advertised.
        appsec_callback(enable_config)
        assert asm_config._asm_enabled
        assert set(_ALL_ASM_BLOCKING) <= advertised()
        assert advertised() == set(_rc_capabilities())

        # One-click deactivation: blocking capabilities must be dropped again.
        appsec_callback(disable_config)
        assert not asm_config._asm_enabled
        assert Cap.AsmActivation in advertised()
        assert advertised().isdisjoint(_ALL_ASM_BLOCKING)
        assert advertised() == set(_rc_capabilities())

    disable_appsec_rc()


def test_rc_activation_validate_products(tracer, rc_poller, appsec_callback):
    with override_global_config(dict(_asm_enabled=False, _remote_config_enabled=True, api_version="v0.4")):
        assert not rc_poller._worker

        enable_appsec_rc(appsec_callback)

        assert rc_poller._client._product_callbacks[RemoteConfigProduct.AsmFeatures]
    disable_appsec_rc()


def test_rc_activation_validate_client_id(tracer, rc_poller, appsec_callback):
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True)
        enable_appsec_rc(appsec_callback)
        _set_and_get_appsec_tags(tracer, True)
    disable_appsec_rc()


def test_rc_client_id_tag_reflects_live_value_not_a_stale_cache(tracer):
    """_dd.rc.client_id must be read live at span-tagging time, not cached once at RC enable time.

    Otherwise the tag would go stale after e.g. an AWS Lambda MicroVM identity refresh
    regenerates the real client id.
    """
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    with override_global_config(dict(_asm_enabled=True, api_version="v0.4")):
        tracer.configure(appsec_enabled=True)

        with mock.patch.object(remoteconfig_poller._client, "id", "client-id-one"):
            with asm_context(tracer) as span:
                set_http_meta(span, {}, raw_uri="http://example.com/", status_code="200")
            assert span._local_root._get_str_attribute(APPSEC.RC_CLIENT_ID) == "client-id-one"

        with mock.patch.object(remoteconfig_poller._client, "id", "client-id-two"):
            with asm_context(tracer) as span:
                set_http_meta(span, {}, raw_uri="http://example.com/", status_code="200")
            assert span._local_root._get_str_attribute(APPSEC.RC_CLIENT_ID) == "client-id-two"


@pytest.mark.parametrize(
    "env_rules, expected",
    [
        ({"_asm_can_be_enabled": True}, True),  # All capabilities
        ({"_asm_static_rule_file": DEFAULT.RULES, "_asm_can_be_enabled": False}, False),  # Only ASM_FEATURES
    ],
)
@pytest.mark.parametrize(
    "enable_config_content, disable_config_content",
    [
        ({"asm": {"enabled": True}}, {"asm": {}}),
        (
            {"asm": {"enabled": True}, "data": [{"id": 1}]},
            {"asm": {}, "data": [{"id": 1}]},
        ),  # additional data in the same product should not change the result
        (
            {"asm": {"enabled": True, "data": 0}},
            {"asm": {"data": 0}},
        ),  # additional data in the same config should not change the result
    ],
)
def test_rc_activation_check_asm_features_product_disables_rest_of_products(
    tracer, rc_poller, env_rules, expected, enable_config_content, disable_config_content, appsec_callback
):
    global_config = dict(_remote_config_enabled=True, _asm_enabled=True)
    global_config.update(env_rules)
    from ddtrace.internal.remoteconfig.client import config as rc_config

    rc_config.skip_shutdown = False
    empty_config = []
    enable_config = [build_payload("ASM_FEATURES", enable_config_content, "config")]
    disable_config = [build_payload("ASM_FEATURES", disable_config_content, "config")]
    with override_global_config(global_config):
        tracer.configure(appsec_enabled=True)
        enable_appsec_rc(appsec_callback)
        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmData)) is expected
        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.Asm)) is expected
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmFeatures)

        # sending nothing should not change anything (configuration is the same)
        appsec_callback(empty_config)

        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmData)) is expected
        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.Asm)) is expected
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmFeatures)

        # sending empty config for asm should disable asm (meaning asm was deleted)
        appsec_callback(disable_config)

        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmData) is None
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.Asm) is None
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmFeatures)

        # sending nothing should not change anything (configuration is the same)
        appsec_callback(empty_config)
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmData) is None
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.Asm) is None
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmFeatures)

        # sending config should enable asm again
        appsec_callback(enable_config)
        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmData)) is expected
        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.Asm)) is expected
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmFeatures)

    disable_appsec_rc()


@pytest.mark.parametrize("auto_user", [True, False])
def test_rc_activation_with_auto_user_appsec_fixed(tracer, rc_poller, auto_user, appsec_callback):
    with (
        override_env({APPSEC.ENV: "true"}),
        override_global_config(
            dict(
                _remote_config_enabled=True,
                _asm_enabled=True,
                _auto_user_instrumentation_enabled=auto_user,
                api_version="v0.4",
            )
        ),
    ):
        tracer.configure(appsec_enabled=True)
        enable_appsec_rc(appsec_callback)

        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmData)
        assert rc_poller._client._product_callbacks.get(RemoteConfigProduct.Asm)
        assert bool(rc_poller._client._product_callbacks.get(RemoteConfigProduct.AsmFeatures)) == auto_user

    disable_appsec_rc()


def test_rc_activation_ip_blocking_data(tracer, rc_poller, appsec_callback):
    with override_global_config({"_asm_enabled": True}):
        rc_config = {
            "rules_data": [
                {
                    "data": [{"expiration": 1755346879, "value": "user8"}],
                    "id": "blocked_users",
                    "type": "data_with_expiration",
                },
                {
                    "data": [
                        {"value": "8.8.4.4"},
                    ],
                    "id": "blocked_ips",
                    "type": "ip_with_expiration",
                },
            ]
        }
        assert rc_poller.status == ServiceStatus.STOPPED

        appsec_callback([build_payload("ASM_DATA", rc_config, "data")])
        with asm_context(tracer, ip_addr="8.8.4.4") as span:
            set_http_meta(
                span,
                rules.Config(),
            )
        assert get_triggers(span)
        assert get_waf_addresses("http.request.remote_ip") == "8.8.4.4"


def test_rc_activation_ip_blocking_data_expired(tracer, rc_poller, appsec_callback):
    with override_env({APPSEC.ENV: "true"}), override_global_config({}):
        tracer.configure(appsec_enabled=True)
        rc_config = {
            "rules_data": [
                {
                    "data": [
                        {"expiration": int(time.time()) - 10000, "value": "8.8.4.4"},
                    ],
                    "id": "blocked_ips",
                    "type": "ip_with_expiration",
                },
            ]
        }

        assert rc_poller.status == ServiceStatus.STOPPED

        appsec_callback([build_payload("ASM_DATA", rc_config, "data")])

        with asm_context(tracer, ip_addr="8.8.4.4") as span:
            set_http_meta(
                span,
                rules.Config(),
            )
        assert get_triggers(span) is None


def test_rc_activation_ip_blocking_data_not_expired(tracer, rc_poller, appsec_callback):
    with override_global_config({"_asm_enabled": True}):
        rc_config = {
            "rules_data": [
                {
                    "data": [
                        {"expiration": int(time.time()) + 10000, "value": "8.8.4.4"},
                    ],
                    "id": "blocked_ips",
                    "type": "ip_with_expiration",
                },
            ]
        }

        assert rc_poller.status == ServiceStatus.STOPPED

        appsec_callback([build_payload("ASM_DATA", rc_config, "data")])

        with asm_context(tracer, ip_addr="8.8.4.4") as span:
            set_http_meta(
                span,
                rules.Config(),
            )
        assert get_triggers(span)
        assert get_waf_addresses("http.request.remote_ip") == "8.8.4.4"


def test_rc_activation_does_not_report_appsec_product_when_only_rc_enabled(tracer, rc_poller, appsec_callback):
    """Regression test: registering RC listeners should not report AppSec as an enabled product in telemetry."""
    with override_global_config(dict(_asm_enabled=False, _asm_can_be_enabled=True, _remote_config_enabled=True)):
        with mock.patch("ddtrace.appsec._remoteconfiguration.telemetry_writer") as mock_tw:
            enable_appsec_rc(appsec_callback)

            # RC listeners are registered but AppSec is not enabled
            assert rc_poller._client._product_callbacks[RemoteConfigProduct.AsmFeatures]
            # Telemetry should NOT report AppSec as activated
            mock_tw.product_activated.assert_not_called()

    disable_appsec_rc()


def test_rc_activation_reports_appsec_product_when_enabled(tracer, rc_poller, appsec_callback):
    """When AppSec is explicitly enabled, enable_appsec_rc should report the product as activated."""
    with override_global_config(dict(_asm_enabled=True, _remote_config_enabled=True)):
        tracer.configure(appsec_enabled=True)
        with mock.patch("ddtrace.appsec._remoteconfiguration.telemetry_writer") as mock_tw:
            enable_appsec_rc(appsec_callback)

            mock_tw.product_activated.assert_called_once_with(TELEMETRY_APM_PRODUCT.APPSEC, True)

    disable_appsec_rc()


def test_enable_asm_reports_telemetry():
    with (
        mock.patch.object(asm_config, "_asm_enabled", False),
        mock.patch.object(asm_config, "_asm_can_be_enabled", True),
        mock.patch("ddtrace.appsec._listeners.load_appsec", return_value=True) as load_appsec,
        mock.patch("ddtrace.internal.telemetry.telemetry_writer.product_activated") as product_activated,
    ):
        _enable_asm()

    load_appsec.assert_called_once_with(reconfigure_tracer=True, origin=APPSEC.ENABLED_ORIGIN_RC)
    product_activated.assert_called_once_with(TELEMETRY_APM_PRODUCT.APPSEC, True)


def test_disable_asm_reports_telemetry():
    def disable_appsec(*, reconfigure_tracer):
        assert reconfigure_tracer is True
        asm_config._asm_enabled = False

    with (
        mock.patch.object(asm_config, "_asm_enabled", True),
        mock.patch("ddtrace.appsec._listeners.disable_appsec", side_effect=disable_appsec),
        mock.patch("ddtrace.internal.telemetry.telemetry_writer.product_activated") as product_activated,
    ):
        _disable_asm()

    product_activated.assert_called_once_with(TELEMETRY_APM_PRODUCT.APPSEC, False)
