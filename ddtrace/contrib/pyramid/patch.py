import os

from .trace import trace_pyramid, DD_TWEEN_NAME

import pyramid.config
from pyramid.path import caller_package

import wrapt


def patch():
    """
    Patch pyramid.config.Configurator
    """
    if getattr(pyramid.config, '_datadog_patch', False):
        return

    setattr(pyramid.config, '_datadog_patch', True)
    _w = wrapt.wrap_function_wrapper
    _w('pyramid.config', 'Configurator.__init__', traced_init)

def traced_init(wrapped, instance, args, kwargs):
    settings = kwargs.pop('settings', {})
    service = os.environ.get('DATADOG_SERVICE_NAME') or 'pyramid'
    trace_settings = {
        'datadog_trace_service' : service,
    }
    settings.update(trace_settings)
    insert_tween_if_needed(settings)
    kwargs['settings'] = settings

    # `caller_package` works by walking a fixed amount of frames up the stack
    # to find the calling package. So if we let the original `__init__`
    # function call it, our wrapper will mess things up.
    if not kwargs.get('package', None):
        kwargs['package'] = caller_package()

    wrapped(*args, **kwargs)
    trace_pyramid(instance)

def insert_tween_if_needed(settings):
    if 'pyramid.tweens' not in settings:
        return
    tweens_list = settings['pyramid.tweens']
    if DD_TWEEN_NAME in tweens_list:
        return
    idx = tweens_list.find(pyramid.tweens.EXCVIEW)
    insert_point = idx + len(pyramid.tweens.EXCVIEW)
    if idx is 0:
        settings['pyramid.tweens'] = DD_TWEEN_NAME + '\n' + tweens_list
    else:
        settings['pyramid.tweens'] = tweens_list[:insert_point]  + '\n' + DD_TWEEN_NAME + tweens_list[insert_point:]
