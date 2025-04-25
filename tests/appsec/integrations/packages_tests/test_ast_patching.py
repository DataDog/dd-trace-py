"""
Some related tests are located in integrations/packages_tests because they require
external packages (e.g., werkzeug) to be installed in the environment.
This is necessary for detection by should_iast_patch,
which checks for packages in site-packages.
"""
import sys

import pytest

from ddtrace.appsec._iast._ast import iastpatch


def test_should_iast_patch_packages_allow():
    assert iastpatch.should_iast_patch("markupsafe") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("pymysql") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("pymysql.charset") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("pymysql.connections") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("pymysql.cursors") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("sqlalchemy") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("sqlalchemy.connectors") == iastpatch.ALLOWED_STATIC_ALLOWLIST
    assert iastpatch.should_iast_patch("sqlalchemy.engine") == iastpatch.ALLOWED_STATIC_ALLOWLIST


def test_should_iast_patch_packages_deny_not_found():
    assert iastpatch.should_iast_patch("pytest") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("hypothesis") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("psycopg2") == iastpatch.DENIED_NOT_FOUND


def test_should_iast_patch_packages_deny_builtins():
    assert iastpatch.should_iast_patch("pytest") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("requests") == iastpatch.DENIED_BUILTINS_DENYLIST


@pytest.mark.skipif(sys.version_info[:2] == (3, 10), reason="Python 3.10 doesn't recognize werkzeug as first_party")
def test_should_iast_patch_packages_allow_werkzeug():
    assert iastpatch.should_iast_patch("werkzeug.utils") == iastpatch.ALLOWED_STATIC_ALLOWLIST


@pytest.mark.skipif(sys.version_info[:2] == (3, 10), reason="Profiling unsupported with 3.13")
def test_should_iast_patch_packages_werkzeug_not_found():
    assert iastpatch.should_iast_patch("werkzeug") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.datastructures") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.datastructures.structures") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.datastructures.ranges") == iastpatch.DENIED_NOT_FOUND
    assert iastpatch.should_iast_patch("werkzeug.utils") != iastpatch.DENIED_NOT_FOUND
