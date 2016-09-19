"""
code to measure django template rendering.
"""


# stdlib
import logging

# project
from ...ext import http

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

    def traced_render(self, context):
        with tracer.trace('django.template', span_type=http.TEMPLATE) as span:
            try:
                return Template._datadog_original_render(self, context)
            finally:
                template_name = self.name or getattr(context, 'template_name', None) or 'unknown'
                span.resource = template_name
                span.set_tag('django.template_name', template_name)

    Template.render = traced_render
