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

long_description = """
# dd-trace-py

`ddtrace` is Datadog's tracing library for Python.  It is used to trace requests
as they flow across web servers, databases and microservices so that developers
have great visiblity into bottlenecks and troublesome requests.

## Getting Started

For a basic product overview, installation and quick start, check out our
[setup documentation][setup docs].

For more advanced usage and configuration, check out our [API
documentation][pypi docs].

For descriptions of terminology used in APM, take a look at the [official
documentation][visualization docs].

[setup docs]: https://docs.datadoghq.com/tracing/setup/python/
[pypi docs]: http://pypi.datadoghq.com/trace/docs/
[visualization docs]: https://docs.datadoghq.com/tracing/visualization/
"""

setup(
    name='ddtrace',
    version=version,
    description='Datadog tracing code',
    url='https://github.com/DataDog/dd-trace-py',
    author='Datadog, Inc.',
    author_email='dev@datadoghq.com',
    long_description=long_description,
    long_description_content_type='text/markdown',
    license='BSD',
    packages=find_packages(exclude=['tests*']),
    install_requires=[
        "wrapt",
        "msgpack-python",
    ],
    extras_require={
        # users can include opentracing by having:
        # install_requires=["ddtrace[opentracing]", ...]
        "opentracing": ["opentracing>=2.0.0"],
    },
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
