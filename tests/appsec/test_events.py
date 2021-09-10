from ddtrace.appsec.internal.events import context


def test_required_context():
    assert context.get_required_context() == context.Context_0_1_0()
