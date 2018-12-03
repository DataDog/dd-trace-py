"""
This file configures a local pytest plugin, which allows us to configure plugin hooks to control the
execution of our tests. Either by loading in fixtures, configuring directories to ignore, etc

Local plugins: https://docs.pytest.org/en/3.10.1/writing_plugins.html#local-conftest-plugins
Hook reference: https://docs.pytest.org/en/3.10.1/reference.html#hook-reference
"""
import os
import re
import sys

PY_DIR_PATTERN = re.compile(r'^py[23][0-9]$')


# Determine if the folder should be ignored
# https://docs.pytest.org/en/3.10.1/reference.html#_pytest.hookspec.pytest_ignore_collect
# DEV: We can only ignore folders/modules, we cannot ignore individual files
# TODO: Should this be `pytest_collect_directory`? or `pytest_collect_file`?
def pytest_ignore_collect(path, config):
    """
    Skip directories defining a required minimum Python version

    Example::

        File: tests/contrib/vertica/py35/test.py
        Python 2.7: Skip
        Python 3.4: Skip
        Python 3.5: Collect
        Python 3.6: Collect

    :rtype: bool
    :returns: ``True`` to skip the directory, ``False`` to collect
    """
    # DEV: `path` is a `LocalPath`
    path = str(path)
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    dirname = os.path.basename(path)

    if len(dirname) == 4 and PY_DIR_PATTERN.match(dirname):
        min_required = tuple((int(v) for v in dirname.strip('py')))
        if sys.version_info[0:2] < min_required:
            return True

    return False
