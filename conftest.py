"""
This file configures a local pytest plugin, which allows us to configure plugin hooks to control the
execution of our tests. Either by loading in fixtures, configuring directories to ignore, etc

Local plugins: https://docs.pytest.org/en/3.10.1/writing_plugins.html#local-conftest-plugins
Hook reference: https://docs.pytest.org/en/3.10.1/reference.html#hook-reference
"""
import os
import re
import sys

import pytest

# DEV: Enable "testdir" fixture https://docs.pytest.org/en/stable/reference.html#testdir
pytest_plugins = ("pytester",)

PY_DIR_PATTERN = re.compile(r"^py[23][0-9]$")


# Hook for dynamic configuration of pytest in CI
# https://docs.pytest.org/en/6.2.1/reference.html#pytest.hookspec.pytest_configure
def pytest_configure(config):
    if os.getenv("CI") != "true":
        return

    # Write JUnit xml results to a file that contains this process' PID
    # This ensures running pytest multiple times does not overwrite previous results
    # e.g. test-results/junit.xml -> test-results/junit.1797.xml
    if config.option.xmlpath:
        fname, ext = os.path.splitext(config.option.xmlpath)
        # DEV: `ext` will contain the `.`, e.g. `.xml`
        config.option.xmlpath = "{0}.{1}{2}".format(fname, os.getpid(), ext)


# Determine if the folder should be ignored
# https://docs.pytest.org/en/3.10.1/reference.html#_pytest.hookspec.pytest_ignore_collect
# DEV: We can only ignore folders/modules, we cannot ignore individual files
# DEV: We must wrap with `@pytest.mark.hookwrapper` to inherit from default (e.g. honor `--ignore`)
#      https://github.com/pytest-dev/pytest/issues/846#issuecomment-122129189
@pytest.mark.hookwrapper
def pytest_ignore_collect(path, config):
    """
    Skip directories defining a required minimum Python version

    Example::

        File: tests/contrib/vertica/py35/test.py
        Python 2.7: Skip
        Python 3.4: Skip
        Python 3.5: Collect
        Python 3.6: Collect
    """
    # Execute original behavior first
    # DEV: We need to set `outcome.force_result(True)` if we need to override
    #      these results and skip this directory
    outcome = yield

    # Was not ignored by default behavior
    if not outcome.get_result():
        # DEV: `path` is a `LocalPath`
        path = str(path)
        if not os.path.isdir(path):
            path = os.path.dirname(path)
        dirname = os.path.basename(path)

        # Directory name match `py[23][0-9]`
        if PY_DIR_PATTERN.match(dirname):
            # Split out version numbers into a tuple: `py35` -> `(3, 5)`
            min_required = tuple((int(v) for v in dirname.strip("py")))

            # If the current Python version does not meet the minimum required, skip this directory
            if sys.version_info[0:2] < min_required:
                outcome.force_result(True)
