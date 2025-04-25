"""
Some related tests are located in integrations/packages_tests because they require
external packages (e.g., werkzeug) to be installed in the environment.
This is necessary for detection by should_iast_patch,
which checks for packages in site-packages.
"""

from ddtrace.appsec._iast._ast import iastpatch


def test_should_iast_patch_allow_packages():
    assert iastpatch.should_iast_patch("werkzeug.utils") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("markupsafe") == iastpatch.ALLOWED_STATIC_ALLOWLIST


def test_should_not_iast_patch_werkzeug():
    assert iastpatch.should_iast_patch("werkzeug") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.datastructures") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.datastructures.structures") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.datastructures.ranges") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.utils") != iastpatch.DENIED_NOT_FOUND
