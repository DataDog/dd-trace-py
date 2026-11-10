import pytest


@pytest.mark.subprocess(parametrize={"BUNDLE_SUPPORT": ["present", "absent"]})
def test_subscriber_benchmarks_support_release_baselines() -> None:
    import os
    import sys
    from unittest.mock import patch

    from ddtrace.internal import core
    from ddtrace.internal.core import subscriber

    sys.path.insert(0, "benchmarks")
    with patch("bm._scenario._register"):
        from benchmarks.events_api.scenario import EventsAPIScenario

    if os.environ["BUNDLE_SUPPORT"] == "absent":
        del subscriber.SubscriberBundle

    for api in ("subscriber_setup", "subscriber_registration", "subscriber_reset_recovery"):
        core.reset_listeners("context.started.web.request")
        core.reset_listeners("context.ended.web.request")
        workload = EventsAPIScenario(name=api, api=api).run()
        next(workload)(2)
        assert next(workload, None) is None
        assert core.has_listeners("context.started.web.request")
        assert core.has_listeners("context.ended.web.request")
