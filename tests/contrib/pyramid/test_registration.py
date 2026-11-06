from unittest.mock import Mock
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("enabled", [True, False])
def test_tween_setup_declares_subscribers_only_when_enabled(enabled):
    from ddtrace.contrib.internal.pyramid.constants import SETTINGS_TRACE_ENABLED
    from ddtrace.contrib.internal.pyramid.trace import trace_tween_factory

    handler = Mock()
    registry = Mock(settings={SETTINGS_TRACE_ENABLED: enabled})
    with patch("ddtrace.internal.core.subscriber.SubscriberBundle.register") as register:
        tween = trace_tween_factory(handler, registry)
    assert register.call_count == int(enabled)
    assert (tween is handler) == (not enabled)
