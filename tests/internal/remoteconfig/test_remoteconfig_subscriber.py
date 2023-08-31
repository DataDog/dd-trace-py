# -*- coding: utf-8 -*-
import os
from time import sleep

import mock

from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from tests.internal.remoteconfig.utils import MockConnector


def test_subscriber_thread():
    os.environ["DD_REMOTECONFIG_POLL_SECONDS"] = "0.1"
    mock_callback = mock.MagicMock()
    subscriber = RemoteConfigSubscriber(MockConnector({"example": "data"}), mock_callback, "TEST_DATA")
    assert not subscriber.is_running

    subscriber.start()
    sleep(0.15)
    assert subscriber.is_running
    mock_callback.assert_called_with({"example": "data"}, test_tracer=None)

    subscriber.stop()
    assert not subscriber.is_running
