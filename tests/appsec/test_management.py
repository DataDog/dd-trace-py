import os.path

import pytest

from ddtrace.ext import priority
from tests.utils import override_env
from tests.utils import override_global_config


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def test_enable(appsec, tracer):
    appsec.enable()

    # Check the Sqreen Library was successfully loaded
    assert appsec._mgmt.enabled

    with tracer.trace("test") as span:
        appsec.process_request(span, method="GET")

    appsec.disable()
    assert not appsec._mgmt.enabled


def test_enable_custom_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-good.json"))):
        appsec.enable()

        # Check the Sqreen Library was successfully loaded
        assert appsec._mgmt.enabled


def test_enable_nonexistent_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "nonexistent"))):
        with pytest.raises(IOError):
            appsec.enable()

        assert not appsec._mgmt.enabled

    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "nonexistent"))):
            # by default enable must not crash but display errors in the logs
            appsec.enable()

            assert not appsec._mgmt.enabled


def test_enable_bad_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-bad.json"))):
        with pytest.raises(ValueError):
            appsec.enable()

        assert not appsec._mgmt.enabled

    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-bad.json"))):
            # by default enable must not crash but display errors in the logs
            appsec.enable()
            assert not appsec._mgmt.enabled


def test_retain_traces(tracer, appsec, appsec_dummy_writer):
    appsec.enable()

    with tracer.trace("test") as span:
        appsec.process_request(span, query="<script>")

    assert span.get_tag("appsec.event") == "true"
    assert span.context.sampling_priority == priority.USER_KEEP
