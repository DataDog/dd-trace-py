from contextlib import contextmanager

import mock
import pytest

from ddtrace.internal.metrics import Metrics


@contextmanager
def mock_metrics(namespace=None):
    metrics = Metrics(namespace=namespace)
    try:
        metrics._client = mock.Mock()
        metrics.enable()
        yield metrics
    finally:
        metrics.disable()


@pytest.mark.parametrize("namespace", [None, "namespace"])
def test_metrics_names(namespace):
    PI = 3.14159265359
    with mock_metrics(namespace) as metrics:
        client = metrics._client
        m = metrics.get_meter("foo.bar")
        m.increment("my.counter")
        m.distribution("my.dist", PI)

    # The meter gets disabled when the context manager returns, so the client
    # should not have these calls
    m.increment("my.counter2")
    m.distribution("my.dist2", PI)

    assert client.increment.mock_calls == [mock.call("foo.bar.my.counter", 1.0, None)]
    assert client.distribution.mock_calls == [mock.call("foo.bar.my.dist", PI, None)]
