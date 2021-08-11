import os.path

import pytest

from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def test_enable(appsec):
    tracer = DummyTracer()
    appsec.enable()

    # Check the Sqreen Library was successfully loaded
    assert len(appsec._mgmt.protections) == 1

    # Load the library after enabling AppSec because it raises at import time
    # if the platform is not supported.
    from ddtrace.appsec.internal.sqreen import SqreenLibrary

    assert isinstance(appsec._mgmt.protections[0], SqreenLibrary)

    with tracer.trace("test") as span:
        appsec.process_request(span, method="GET")

    appsec.disable()
    assert appsec._mgmt.protections == []


def test_enable_custom_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-good.json"))):
        appsec.enable()

        # Check the Sqreen Library was successfully loaded
        assert len(appsec._mgmt.protections) == 1

        # Load the library after enabling AppSec because it raises at import time
        # if the platform is not supported.
        from ddtrace.appsec.internal.sqreen import SqreenLibrary

        assert isinstance(appsec._mgmt.protections[0], SqreenLibrary)


def test_enable_nonexistent_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "nonexistent"))):
        with pytest.raises(IOError):
            appsec.enable()
        assert appsec._mgmt.protections == []

    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "nonexistent"))):
            # by default enable must not crash but display errors in the logs
            appsec.enable()
            assert appsec._mgmt.protections == []


def test_enable_bad_rules(appsec):
    with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-bad.json"))):
        with pytest.raises(ValueError):
            appsec.enable()
        assert appsec._mgmt.protections == []

    with override_global_config(dict(_raise=False)):
        with override_env(dict(DD_APPSEC_RULES=os.path.join(ROOT_DIR, "rules-bad.json"))):
            # by default enable must not crash but display errors in the logs
            appsec.enable()
            assert appsec._mgmt.protections == []
