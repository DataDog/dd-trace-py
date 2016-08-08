from ddtrace import __version__

from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand

import os
import sys

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
        #import here, cause outside the eggs aren't loaded
        import tox
        import shlex
        args = self.tox_args
        if args:
            args = shlex.split(self.tox_args)
        errno = tox.cmdline(args=args)
        sys.exit(errno)


version = __version__
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
        "wrapt"
    ],
    # plugin tox
    tests_require=['tox'],
    cmdclass = {'test': Tox},
)

