# -*- coding: utf-8 -*-
from time import sleep

import mock

from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from tests.internal.remoteconfig.utils import MockConnector
from tests.utils import override_global_config


def test_subscriber_thread():
    with override_global_config(dict(_remote_config_poll_interval=0.1)):
        mock_callback = mock.MagicMock()
        subscriber = RemoteConfigSubscriber(MockConnector({"example": "data"}), mock_callback, "TEST_DATA")
        assert not subscriber.is_running

        subscriber.start()
        sleep(0.15)
        assert subscriber.is_running
        mock_callback.assert_called_with({"example": "data"}, test_tracer=None)

        subscriber.stop()
        assert not subscriber.is_running
