import os
import sys
import re

from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand


def get_version(package):
    """
    Return package version as listed in `__version__` in `__init__.py`.
    This method prevents to import packages at setup-time.
    """
    init_py = open(os.path.join(package, '__init__.py')).read()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", init_py).group(1)


class Tox(TestCommand):

    user_options = [('tox-args=', 'a', "Arguments to pass to tox")]

    def initialize_options(self):
        TestCommand.initialize_options(self)
        self.tox_args = None

    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        # import here, cause outside the eggs aren't loaded
        import tox
        import shlex
        args = self.tox_args
        if args:
            args = shlex.split(self.tox_args)
        errno = tox.cmdline(args=args)
        sys.exit(errno)


version = get_version('ddtrace')
# Append a suffix to the version for dev builds
if os.environ.get('VERSION_SUFFIX'):
    version = '{v}+{s}'.format(
        v=version,
        s=os.environ.get('VERSION_SUFFIX'),
    )

setup(
    name='ddtrace',
    version=version,
    description='Datadog tracing code',
    url='https://github.com/DataDog/dd-trace-py',
    author='Datadog, Inc.',
    author_email='dev@datadoghq.com',
    license='BSD',
    packages=find_packages(exclude=['tests*']),
    install_requires=[
        "wrapt",
        "msgpack-python",
    ],
    # plugin tox
    tests_require=['tox', 'flake8'],
    cmdclass={'test': Tox},
    entry_points={
        'console_scripts': [
            'ddtrace-run = ddtrace.commands.ddtrace_run:main'
        ]
    },
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
)
