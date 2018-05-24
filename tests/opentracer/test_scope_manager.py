from ddtrace.opentracer.scope_manager import ScopeManager


class TestScopeManager(object):
    def test_init(self):
        scope_manager = ScopeManager()
        assert scope_manager is not None
