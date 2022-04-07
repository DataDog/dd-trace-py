from ddtrace.internal.module import ModuleWatchdog


def on_run_module(run_code):
    def _(*args, **kwargs):
        # Add on_run_module_defined to the globals.
        args[1]["on_run_module_defined"] = True
        return run_code(*args, **kwargs)

    return _


ModuleWatchdog.install(on_run_module=on_run_module)
