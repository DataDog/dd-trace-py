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

from .test_pyramid import PyramidTestCase


class TestPyramidAutopatch(PyramidTestCase):
    instrument = False


class TestPyramidExplicitTweens(PyramidTestCase):
    instrument = False

    def get_settings(self):
        return {
            'pyramid.tweens': 'pyramid.tweens.excview_tween_factory\n',
        }


def _include_me(config):
    pass


def test_config_include():
    """ This test makes sure that relative imports still work when the
    application is run with ddtrace-run """
    config = Configurator()
    config.include('._include_me')
