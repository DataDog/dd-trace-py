from unittest.mock import Mock
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("enabled", [True, False])
def test_tween_setup_does_not_register_subscribers(enabled):
    from ddtrace.contrib.internal.pyramid.constants import SETTINGS_TRACE_ENABLED
    from ddtrace.contrib.internal.pyramid.trace import trace_tween_factory

    handler = Mock()
    registry = Mock(settings={SETTINGS_TRACE_ENABLED: enabled})
    with patch("ddtrace.internal.core.subscriber.SubscriberBundle.register") as register:
        tween = trace_tween_factory(handler, registry)
    register.assert_not_called()
    assert (tween is handler) == (not enabled)
