import sys


def init_handled_exceptions_reporting():
    if sys.version_info < (3, 10):
        return

    if sys.version_info >= (3, 12):
        from ddtrace.internal.error_reporting.handled_exceptions_after_3_12 import _install_sys_monitoring_reporting

        _install_sys_monitoring_reporting()
    elif sys.version_info >= (3, 10):
        from ddtrace.internal.error_reporting.handled_exceptions_before_3_12 import (
            _install_bytecode_injection_reporting,
        )

        _install_bytecode_injection_reporting()


init_handled_exceptions_reporting()
