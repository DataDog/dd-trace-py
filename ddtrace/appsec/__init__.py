from ddtrace.appsec.internal.management import Management


_mgmt = Management()

# Public API
enable = _mgmt.enable
disable = _mgmt.disable
process_request = _mgmt.process_request

__all__ = ["enable", "disable", "process_request"]
