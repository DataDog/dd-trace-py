from ddtrace.appsec.management import Management


mgmt = Management()

# Public API
enable = mgmt.enable
disable = mgmt.disable
process_request = mgmt.process_request

__all__ = ["enable", "disable", "process_request"]
