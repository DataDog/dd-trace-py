from typing import Callable
from typing import Optional


class uWSGIConfigError(Exception):
    """uWSGI configuration error.

    This is raised when uwsgi configuration is incompatible with the library.
    """


class uWSGIConfigDeprecationWarning(DeprecationWarning):
    """uWSGI configuration deprecation warning.

    This is raised when uwsgi configuration is incompatible with the library,
    and future versions of the library plans to raise an error and stop
    supporting the configuration.
    """


class uWSGIMasterProcess(Exception):
    """The process is uWSGI master process."""


def should_register_atexit() -> bool:
    """Check if Python atexit handlers should be registered under uwsgi.

    Returns False if running under uwsgi with --skip-atexit, True otherwise.
    This respects the user's explicit request to skip atexit handlers, which
    is important because running complex operations (like HTTP uploads) during
    atexit can cause heap corruption during process shutdown.
    """
    try:
        import uwsgi

        return not uwsgi.opt.get("skip-atexit")
    except (ImportError, AttributeError):
        # Not running under uwsgi or uwsgi.opt not available
        return True


def check_uwsgi(
    worker_callback: Optional[Callable] = None,
    atexit: Optional[Callable] = None,
    *,
    defer_in_master: bool = False,
) -> None:
    """Check whether uwsgi is running and what needs to be done.

    :param worker_callback: Callback function to call in uWSGI worker processes.
    :param defer_in_master: Defer startup in a non-lazy, multi-process master even when Python fork hooks are enabled.
    """
    try:
        import uwsgi
    except ImportError:
        return

    if not hasattr(uwsgi, "opt"):
        msg = "Unable to access uwsgi options. Please make sure that the --import=ddtrace.auto option is set"
        raise uWSGIConfigError(msg)

    if not (uwsgi.opt.get("enable-threads") or int(uwsgi.opt.get("threads") or 0)):
        msg = "enable-threads option must be set to true, or a positive number of threads must be set"
        raise uWSGIConfigError(msg)

    if (
        hasattr(uwsgi, "version_info")
        and uwsgi.version_info < (2, 0, 30)
        and (uwsgi.opt.get("lazy-apps") or uwsgi.opt.get("lazy"))
        and not uwsgi.opt.get("skip-atexit")
    ):
        msg = "skip-atexit option must be set when lazy-apps or lazy is set for \
            uwsgi<2.0.30, see https://github.com/unbit/uwsgi/pull/2726. We plan \
            to raise an error in ddtrace 4.x release."
        raise uWSGIConfigDeprecationWarning(msg)

    # uwsgi forks worker processes with a raw fork() call at the C level, which
    # bypasses Python's os.register_at_fork machinery. The
    # `py-call-uwsgi-fork-hooks` option closes that gap: uwsgi calls
    # PyOS_BeforeFork/AfterFork_Parent/ AfterFork_Child itself around every
    # worker fork. It only does this when threads are enabled
    # (uwsgi.has_threads), which is already required above.
    fork_hooks_active = bool(uwsgi.opt.get("py-call-uwsgi-fork-hooks"))

    # Python fork hooks already run the general forksafe registry. Registering that registry with
    # uwsgidecorators as well would run it twice in each child. Callers such as the profiler can
    # explicitly defer their own startup: uWSGI owns master finalization, so Python cleanup is not
    # guaranteed to stop profiler threads before native state is destroyed.
    if (
        uwsgi.numproc > 1
        and not uwsgi.opt.get("lazy-apps")
        and (not fork_hooks_active or defer_in_master)
        and uwsgi.worker_id() == 0
    ):
        if not uwsgi.opt.get("master"):
            if fork_hooks_active:
                return
            # Having multiple workers without the master process is not supported here: we rely on
            # uwsgidecorators.postfork to run our callback in each worker, and uwsgidecorators itself
            # refuses to work without a master process (it checks uwsgi.masterpid() == 0). The
            # py-call-uwsgi-fork-hooks option is the supported no-master alternative.
            raise uWSGIConfigError(
                "master option must be enabled when multiple processes are used, unless the "
                "py-call-uwsgi-fork-hooks option is also enabled"
            )

        # Register the function to be called in child process at startup
        if worker_callback is not None:
            try:
                import uwsgidecorators
            except ImportError:
                raise uWSGIConfigError("Running under uwsgi but uwsgidecorators cannot be imported")
            uwsgidecorators.postfork(worker_callback)

        if atexit is not None:
            original_atexit = getattr(uwsgi, "atexit", None)

            def _atexit():
                try:
                    atexit()
                except Exception:
                    pass

                if original_atexit is not None:
                    original_atexit()

            uwsgi.atexit = _atexit

        raise uWSGIMasterProcess()
