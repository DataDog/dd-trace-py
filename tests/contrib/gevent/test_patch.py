import unittest

from ddtrace.contrib.gevent import patch, unpatch

from tests.contrib import PatchMixin


class TestGeventPatch(unittest.TestCase, PatchMixin):

    def tearDown(self):
        pass

    def test_patch(self):
        patch()
        import gevent
        from ddtrace.contrib.gevent.greenlet import TracedGreenlet, TracedIMap, TracedIMapUnordered

        def f():
            pass

        self.assertTrue(isinstance(gevent.greenlet.Greenlet(), TracedGreenlet))
        self.assertTrue(isinstance(gevent.pool.Group.greenlet_class(), TracedGreenlet))
        self.assertTrue(isinstance(gevent.Greenlet(), TracedGreenlet))
        self.assertTrue(isinstance(gevent.pool.IMap(f, []), TracedIMap))
        self.assertTrue(isinstance(gevent.pool.IMapUnordered(f, []), TracedIMapUnordered))

    def test_patch_idempotent(self):
        """
        With the style of patching the gevent integration does, it does not
        make sense to test idempotence.
        """
        pass

    def test_unpatch(self):
        patch()
        unpatch()

        import gevent
        from ddtrace.contrib.gevent.greenlet import TracedGreenlet, TracedIMap, TracedIMapUnordered

        def f():
            pass

        self.assertFalse(isinstance(gevent.greenlet.Greenlet(), TracedGreenlet))
        self.assertFalse(isinstance(gevent.pool.Group.greenlet_class(), TracedGreenlet))
        self.assertFalse(isinstance(gevent.Greenlet(), TracedGreenlet))
        self.assertFalse(isinstance(gevent.pool.IMap(f, []), TracedIMap))
        self.assertFalse(isinstance(gevent.pool.IMapUnordered(f, []), TracedIMapUnordered))
