import os
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


def forked_without_fork_hooks() -> bool:
    """Check if the uwsgi master forked this process without running the Python fork hooks.

    Without lazy-apps, py-call-uwsgi-fork-hooks or py-call-osafterfork, uwsgi forks its workers with
    a raw fork() call, so PyOS_AfterFork_Child does not run in them. The interpreter of the worker
    then still lists the thread states of the other threads of the master. These threads do not
    exist in the worker, and the threads that the worker starts reuse their stacks. CPython APIs
    that walk the frames of every thread state, such as sys.monitoring.set_events and
    sys.monitoring.restart_events, read this memory and can crash.
    """
    try:
        import uwsgi
    except ImportError:
        return False

    try:
        masterpid = uwsgi.masterpid()
        opt = uwsgi.opt
    except AttributeError:
        return False

    return (
        masterpid != 0
        and os.getppid() == masterpid
        and not (opt.get("lazy-apps") or opt.get("lazy"))
        and not (opt.get("py-call-uwsgi-fork-hooks") or opt.get("py-call-osafterfork"))
    )


def will_fork_without_fork_hooks() -> bool:
    """Check if this is a uwsgi master that will fork its workers without running the Python fork hooks.

    uwsgi releases the GIL in the master and then forks it with a raw fork() call. A worker forked while
    another thread of the master holds the GIL waits for it forever, so the master must not have Python
    threads running at that point.
    """
    try:
        import uwsgi
    except ImportError:
        return False

    try:
        opt = uwsgi.opt
        forks = uwsgi.numproc > 1 or opt.get("master")
        in_master = uwsgi.worker_id() == 0
    except AttributeError:
        return False

    return bool(
        forks
        and in_master
        and not (opt.get("lazy-apps") or opt.get("lazy"))
        and not opt.get("py-call-uwsgi-fork-hooks")
    )


def check_uwsgi(worker_callback: Optional[Callable] = None, atexit: Optional[Callable] = None) -> None:
    """Check whetever uwsgi is running and what needs to be done.

    :param worker_callback: Callback function to call in uWSGI worker processes.
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

    # If uwsgi has more than one process, or a master process (which forks its workers, even if there is only one),
    # it is running in prefork operational mode: uwsgi is going to fork sub-processes.
    # If lazy-app is enabled, then the app is loaded in each subprocess independently. This is fine.
    # If py-call-uwsgi-fork-hooks is enabled (see above), regular fork-safety handling applies. This is fine.
    # Otherwise, the app will be loaded in the master process, and uwsgi will `fork()` abruptly, bypassing Python
    # sanity checks. We need to handle this case properly.
    # The proper way to handle that is to allow to register a callback function to run in the subprocess at their
    # startup, and warn the caller that this is the master process and that (probably) nothing should be done.
    if (
        (uwsgi.numproc > 1 or uwsgi.opt.get("master"))
        and not uwsgi.opt.get("lazy-apps")
        and not fork_hooks_active
        and uwsgi.worker_id() == 0
    ):
        if not uwsgi.opt.get("master"):
            # Having multiple workers without the master process is not supported here: we rely on
            # uwsgidecorators.postfork to run our callback in each worker, and uwsgidecorators itself
            # refuses to work without a master process (it checks uwsgi.masterpid() == 0). uwsgi's own
            # fork/postfork mechanism does not actually require a master to function, but we don't bypass
            # uwsgidecorators to work around that -- the py-call-uwsgi-fork-hooks option below is the
            # supported way to run multiple processes without --master.
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
