# 3rd party
from django.db import connections
from django.template import Template

# project
from ddtrace.tracer import Tracer

# testing
from ...test_tracer import DummyWriter


# testing tracer
tracer = Tracer()
tracer.writer = DummyWriter()


def unpatch_template():
    """
    Remove tracing from the Django template engine
    """
    if hasattr(Template, '_datadog_original_render'):
        Template.render = Template._datadog_original_render
        del Template._datadog_original_render


def unpatch_connection():
    """
    Remove tracing from the Django connection engine
    """
    for conn in connections.all():
        if hasattr(conn, '_datadog_original_cursor'):
            conn.cursor = conn._datadog_original_cursor
            del conn._datadog_original_cursor
