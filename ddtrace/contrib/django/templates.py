"""
code to measure django template rendering.
"""


# stdlib
import logging

# project
from ...ext import http, errors

# 3p
from django.template import Template


log = logging.getLogger(__name__)


def patch_template(tracer):
    """ will patch django's template rendering function to include timing
        and trace information.
    """

    # FIXME[matt] we're patching the template class here. ideally we'd only
    # patch so we can use multiple tracers at once, but i suspect this is fine
    # in practice.
    attr = '_datadog_original_render'
    if getattr(Template, attr, None):
        log.debug("already patched")
        return

    setattr(Template, attr, Template.render)

    class TracedTemplate(object):

        def render(self, context):
            with tracer.trace('django.template', span_type=http.TEMPLATE) as span:
                try:
                    return Template._datadog_original_render(self, context)
                finally:
                    span.set_tag('django.template_name', context.template_name or 'unknown')

    Template.render = TracedTemplate.render.__func__

