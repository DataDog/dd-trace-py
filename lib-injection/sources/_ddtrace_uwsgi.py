import os
import sys


def _register_postfork(uwsgi, inject):
    previous_callback = getattr(uwsgi, "post_fork_hook", None)
    import uwsgidecorators

    chain_hook = uwsgidecorators.postfork_chain_hook

    def inject_after_previous():
        try:
            if previous_callback is not None and previous_callback is not chain_hook:
                previous_callback()
        finally:
            inject()

    uwsgidecorators.postfork(inject_after_previous)
    uwsgi.post_fork_hook = chain_hook


def _defer_in_master(uwsgi, inject):
    if not hasattr(uwsgi, "opt"):
        _register_postfork(uwsgi, inject)
        return True

    prefork_master = uwsgi.worker_id() == 0 and uwsgi.opt.get("master")
    if prefork_master:
        _register_postfork(uwsgi, inject)
    return prefork_master


# AIDEV-NOTE: Some uWSGI/Python combinations run sitecustomize before the C extension publishes the uwsgi module.
# A temporary trace or profile callback waits for the module without importing ddtrace or replacing an active hook.
def _wait_for_uwsgi_module(inject):
    if sys.gettrace() is None:
        set_hook = sys.settrace
    elif sys.getprofile() is None:
        set_hook = sys.setprofile
    else:
        raise RuntimeError("cannot defer uWSGI injection while trace and profile functions are active")

    def wait_for_module(frame, event, arg):
        uwsgi = sys.modules.get("uwsgi")
        masterpid = getattr(uwsgi, "masterpid", None)
        if masterpid is not None and (hasattr(uwsgi, "opt") or masterpid() != 0):
            set_hook(None)
            if not _defer_in_master(uwsgi, inject):
                inject()
            return None
        return wait_for_module

    set_hook(wait_for_module)


def _is_uwsgi_executable(candidate):
    if not candidate:
        return False
    name = os.path.basename(candidate).lower()
    return name in ("uwsgi", "uwsgi-core") or name.startswith(("uwsgi-python", "uwsgi_python"))


def _is_uwsgi_process():
    try:
        return _is_uwsgi_executable(os.readlink("/proc/self/exe"))
    except OSError:
        pass

    candidates = [sys.executable]
    if sys.argv:
        candidates.append(sys.argv[0])
    return any(_is_uwsgi_executable(candidate) for candidate in candidates)


def defer_injection(inject):
    try:
        import uwsgi
    except ImportError:
        if not _is_uwsgi_process():
            return False
        _wait_for_uwsgi_module(inject)
        return True

    if not hasattr(uwsgi, "masterpid"):
        _wait_for_uwsgi_module(inject)
        return True

    return _defer_in_master(uwsgi, inject)
