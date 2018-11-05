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

        self.assertIsInstance(gevent.greenlet.Greenlet(), TracedGreenlet)
        self.assertIsInstance(gevent.pool.Group.greenlet_class(), TracedGreenlet)
        self.assertIsInstance(gevent.Greenlet(), TracedGreenlet)
        self.assertIsInstance(gevent.pool.IMap(f, []), TracedIMap)
        self.assertIsInstance(gevent.pool.IMapUnordered(f, []), TracedIMapUnordered)

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

        self.assertNotIsInstance(gevent.greenlet.Greenlet(), TracedGreenlet)
        self.assertNotIsInstance(gevent.pool.Group.greenlet_class(), TracedGreenlet)
        self.assertNotIsInstance(gevent.Greenlet(), TracedGreenlet)
        self.assertNotIsInstance(gevent.pool.IMap(f, []), TracedIMap)
        self.assertNotIsInstance(gevent.pool.IMapUnordered(f, []), TracedIMapUnordered)
