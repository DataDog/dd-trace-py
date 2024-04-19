"""
[package_name]==[version]

https://pypi.org/project/[package_name]/

[Description]
"""
from flask import Blueprint, request
from tests.utils import override_env

with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

pkg_[package_name] = Blueprint('package_[package_name]', __name__)


@pkg_[package_name].route('/[package_name]')
def pkg_[package_name]_view():+
    import [package_name]
    package_param = request.args.get("package_param")

    [CODE]

    return {
        "param": package_param,
        "params_are_tainted": is_pyobject_tainted(package_param)
    }

