import gc
import os
import ddtrace

__gc_span = None
__gc_span_pid = None

def __gc_stats_apm_callback(phase, info):
    """
    A callback that conforms to the python 3.3+ gc callback mechanism
    https://docs.python.org/3/library/gc.html#gc.callbacks
    """
    global __gc_span
    global __gc_span_pid
    
    # To ensure fork-safety we throw away any existing span if we've been forked
    # A cleaner approach will be possible in Py3.7+
    # https://docs.python.org/3.7/library/os.html#os.register_at_fork
    pid = os.getpid()

    if __gc_span_pid != pid:
        __gc_span = None
        __gc_span_pid = pid

    generation = str(info.get('generation', 'unknown')) if info else 'unknown'

    if phase == 'start' and not __gc_span:
        __gc_span = ddtrace.tracer.trace(
            'gc', service='python.gc', resource='gc_generation_{}'.format(generation), span_type='python')
    elif phase == 'stop' and __gc_span::
        __gc_span.set_tags({
            'generation':       generation,
            'collected':        info.get('collected', 0) if info else 0,
            'uncollectable':    info.get('uncollectable', 0) if info else 0,
        })
        __gc_span.finish()
        __gc_span = None

def patch():
    if __gc_stats_apm_callback not in gc.callbacks:
        gc.callbacks.append(__gc_stats_apm_callback)

def unpatch():
    if __gc_stats_apm_callback in gc.callbacks:
        gc.callbacks.remove(__gc_stats_apm_callback)
