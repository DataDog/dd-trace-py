# stdlib
import sys
import webtest
import ddtrace

from nose.tools import eq_
from pyramid.config import Configurator

# 3p
from wsgiref.simple_server import make_server

# project
from ...test_tracer import get_dummy_tracer
from ...util import override_global_tracer

from .test_pyramid import PyramidBase, get_app, custom_exception_view


class TestPyramidAutopatch(PyramidBase):
    def setUp(self):
        self.tracer = get_dummy_tracer()
        ddtrace.tracer = self.tracer

        config = Configurator()
        self.rend = config.testing_add_renderer('template.pt')
        # required to reproduce a regression test
        config.add_notfound_view(custom_exception_view)
        app = get_app(config)
        self.app = webtest.TestApp(app)


class TestPyramidExplicitTweens(PyramidBase):
    def setUp(self):
        self.tracer = get_dummy_tracer()
        ddtrace.tracer = self.tracer

        config = Configurator(settings={'pyramid.tweens': 'pyramid.tweens.excview_tween_factory\n'})
        self.rend = config.testing_add_renderer('template.pt')
        # required to reproduce a regression test
        config.add_notfound_view(custom_exception_view)
        app = get_app(config)
        self.app = webtest.TestApp(app)


def _include_me(config):
    pass


def includeme(config):
    pass


def test_config_include():
    """ This test makes sure that relative imports still work when the
    application is run with ddtrace-run """
    config = Configurator()
    config.include('._include_me')


def test_include_conflicts():
    """ Test that includes do not create conflicts """
    from ...test_tracer import get_dummy_tracer
    from ...util import override_global_tracer
    tracer = get_dummy_tracer()
    with override_global_tracer(tracer):
        config = Configurator(settings={'pyramid.includes': 'tests.contrib.pyramid.test_pyramid_autopatch'})
        app = webtest.TestApp(config.make_wsgi_app())
        app.get('/', status=404)
        spans = tracer.writer.pop()
        assert spans
        eq_(len(spans), 1)
