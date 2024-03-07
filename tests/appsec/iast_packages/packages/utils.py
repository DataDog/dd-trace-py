from tests.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted


class ResultResponse:
    package_param = ""
    result1 = ""
    result2 = ""

    def __init__(self, package_param):
        self.package_param = package_param

    def json(self):
        return {
            "param": self.package_param,
            "result1": self.result1,
            "result2": self.result2,
            "params_are_tainted": is_pyobject_tainted(self.package_param),
        }
