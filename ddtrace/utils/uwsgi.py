import logging
from ..internal.logger import get_logger

logging.basicConfig(level=logging.DEBUG)

log = get_logger(__name__)


def check_threads_enabled():
    u"""
    Check if uWSGI is in use and whether or not threads are enabled.

    This function is a helper used to confirm whether or not the traced application is
    running via uWSGI, and if so, to further confirm if the necessary config of ``enable-threads``
    has been set and that it's value is true.  If uWSGI is in use, it will return True, otherwise
    False.

    Per uWSGI documentation, attempting to import the module is a recommended way of
    checking whether or not the application is running under uWSGI:
    `uWSGI Python Module Docs <https://uwsgi-docs.readthedocs.io/en/latest/PythonModule.html#uwsgi.opt>`
        Also useful for detecting whether you’re actually running under uWSGI; if you attempt to import
        uwsgi and receive an ImportError you’re not running under uWSGI.
    """
    try:
        import uwsgi
        if uwsgi.opt.get('enable-threads') is None:
            log.debug(
                '--enable-threads not set but needs to be true in uwsgi config for ddtrace to run')
        elif uwsgi.opt['enable-threads'] is not True:
            log.debug('enable-threads=true is required in uwsgi config for ddtrace to run')
        elif uwsgi.opt['enable-threads']:
            return True
    except ImportError:
        return False
