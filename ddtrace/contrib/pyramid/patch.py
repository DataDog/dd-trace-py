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
    # If the tweens are explicitely set with 'pyramid.tweens', we need to
    # explicitly set our tween too since `add_tween` will be ignored.
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
    # If the list is empty, pyramid does not consider the tweens have been
    # set explicitely so we will insert the tween with add_tween.
    if not tweens_list.strip():
        return
    # If the our tween is already there, nothing to do
    if DD_TWEEN_NAME in tweens_list:
        return
    idx = tweens_list.find(pyramid.tweens.EXCVIEW)
    if idx is -1:
        settings['pyramid.tweens'] = tweens_list + '\n' + DD_TWEEN_NAME
    else:
        settings['pyramid.tweens'] = tweens_list[:idx] + DD_TWEEN_NAME + "\n" + tweens_list[idx:]
