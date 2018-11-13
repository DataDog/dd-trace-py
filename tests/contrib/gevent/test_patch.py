import unittest

from ddtrace import patch

from tests.contrib import PatchMixin


class TestGeventPatch(PatchMixin, unittest.TestCase):
    def assert_patched(self, gevent):
        from ddtrace.contrib.gevent.greenlet import TracedGreenlet, TracedIMap, TracedIMapUnordered

        def f():
            pass
        self.assertIsInstance(gevent.greenlet.Greenlet(), TracedGreenlet)
        self.assertTrue(gevent.pool.Group.greenlet_class is TracedGreenlet)
        self.assertIsInstance(gevent.Greenlet(), TracedGreenlet)
        self.assertTrue(gevent.pool.IMap is TracedIMap)
        self.assertTrue(gevent.pool.IMapUnordered is TracedIMapUnordered)

    def assert_not_patched(self, gevent):
        from ddtrace.contrib.gevent.greenlet import TracedGreenlet, TracedIMap, TracedIMapUnordered

        def f():
            pass

        self.assertNotIsInstance(gevent.greenlet.Greenlet(), TracedGreenlet)
        self.assertFalse(gevent.pool.Group.greenlet_class is TracedGreenlet)
        self.assertNotIsInstance(gevent.Greenlet(), TracedGreenlet)
        self.assertFalse(gevent.pool.IMap is TracedIMap)
        self.assertFalse(gevent.pool.IMapUnordered is TracedIMapUnordered)

    def test_patch_before_import(self):
        trigger_reload = self.module_imported('gevent')
        patch(gevent=True)
        import gevent
        if trigger_reload:
            self.reload_module(gevent)
        self.assert_patched(gevent)

    def test_patch_after_import(self):
        import gevent
        patch(gevent=True)
        self.assert_patched(gevent)

    def test_patch_idempotent(self):
        """
        With the style of patching the gevent integration does, it does not
        make sense to test idempotence.
        """
        pass

    def test_unpatch_before_import(self):
        from ddtrace.contrib.gevent import unpatch
        patch(gevent=True)
        unpatch()
        import gevent
        self.assert_not_patched(gevent)

    def test_unpatch_after_import(self):
        from ddtrace.contrib.gevent import unpatch
        patch(gevent=True)
        import gevent
        unpatch()
        self.assert_not_patched(gevent)
