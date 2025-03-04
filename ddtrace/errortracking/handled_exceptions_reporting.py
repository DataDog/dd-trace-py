import sys

from ddtrace.settings.errortracking import config


def init_handled_exceptions_reporting():
    if config.enabled is False:
        return

    if sys.version_info < (3, 10):
        return

    if sys.version_info < (3, 12):
        from ddtrace.errortracking.handled_exceptions_before_3_12 import _install_bytecode_injection_reporting

        """
        For python3.10 and python3.11, handled exceptions reporting is based on bytecode injection.
        This is efficient, as we will instrument only the targeted code. However, it is considered
        unsafe as it could alter the program behavior.Therefore, it will drop for sys.monitoring in
        3.12 (before handled exception events are not supported)
        """
        _install_bytecode_injection_reporting()
    else:
        from ddtrace.errortracking.handled_exceptions_after_3_12 import _install_sys_monitoring_reporting

        """
        Starting from python3.12, handled exceptions reporting is based on sys.monitoring. This is
        safer than bytecode injection as it can not alter the behaviour of a program. However,
        sys.monitoring reports every handled exceptions including python internal ones. Therefore,
        we need to add a filtering step which can be time efficient.
        """
        _install_sys_monitoring_reporting()


init_handled_exceptions_reporting()
