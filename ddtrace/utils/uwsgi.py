import logging

from ddtrace import _worker
from ..internal.logger import get_logger

logging.basicConfig(level=logging.DEBUG)

log = get_logger(__name__)


def check_threads_enabled():
    """
    This function is a helper used to confirm whether or not the traced application is 
    running via uWSGI, and if so, to further confirm if the necessary config of `enable-threads`
    has been set and that it's value is true.  Per uWSGI documentation, attempting to import the
    module is a recommended way of checking whether or not the application is running under uWSGI:
    https://uwsgi-docs.readthedocs.io/en/latest/PythonModule.html#uwsgi.opt - “Also useful
    for detecting whether you’re actually running under uWSGI; if you attempt to import
    uwsgi and receive an ImportError you’re not running under uWSGI.
    """
    try:
        import uwsgi
        if not uwsgi.opt.get('enable-threads'):
            log.debug(
                '--enable-threads not set but needs to be true in uwsgi config for ddtrace to run')
        elif uwsgi.opt['enable-threads'] != True:
            log.debug(
                'enable-threads=true is required in uwsgi config for ddtrace to run')
    except:
        pass
