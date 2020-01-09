import copy
import os
import sys

from distutils.command.build_ext import build_ext
from distutils.errors import CCompilerError, DistutilsExecError, DistutilsPlatformError
from setuptools import setup, find_packages, Extension
from setuptools.command.test import test as TestCommand


class Tox(TestCommand):

    user_options = [("tox-args=", "a", "Arguments to pass to tox")]

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

# psutil used to generate runtime metrics for tracer
# enum34 is an enum backport for earlier versions of python
# funcsigs backport required for vendored debtcollector
install_requires = ["psutil>=5.0.0", "enum34; python_version<'3.4'", "funcsigs>=1.0.0;python_version=='2.7'"]

# Base `setup()` kwargs without any C-extension registering
setup_kwargs = dict(
    name="ddtrace",
    description="Datadog tracing code",
    url="https://github.com/DataDog/dd-trace-py",
    author="Datadog, Inc.",
    author_email="dev@datadoghq.com",
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="BSD",
    packages=find_packages(exclude=["tests*"]),
    install_requires=install_requires,
    extras_require={
        # users can include opentracing by having:
        # install_requires=['ddtrace[opentracing]', ...]
        "opentracing": ["opentracing>=2.0.0"],
    },
    # plugin tox
    tests_require=["tox", "flake8"],
    cmdclass={"test": Tox},
    entry_points={"console_scripts": ["ddtrace-run = ddtrace.commands.ddtrace_run:main"]},
    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
)


# The following from here to the end of the file is borrowed from wrapt's and msgpack's `setup.py`:
#   https://github.com/GrahamDumpleton/wrapt/blob/4ee35415a4b0d570ee6a9b3a14a6931441aeab4b/setup.py
#   https://github.com/msgpack/msgpack-python/blob/381c2eff5f8ee0b8669fd6daf1fd1ecaffe7c931/setup.py
# These helpers are useful for attempting build a C-extension and then retrying without it if it fails

libraries = []
if sys.platform == "win32":
    libraries.append("ws2_32")
    build_ext_errors = (CCompilerError, DistutilsExecError, DistutilsPlatformError, IOError, OSError)
else:
    build_ext_errors = (CCompilerError, DistutilsExecError, DistutilsPlatformError)


class BuildExtFailed(Exception):
    pass


# Attempt to build a C-extension, catch and throw a common/custom error if there are any issues
class optional_build_ext(build_ext):
    def run(self):
        try:
            build_ext.run(self)
        except DistutilsPlatformError:
            raise BuildExtFailed()

    def build_extension(self, ext):
        try:
            build_ext.build_extension(self, ext)
        except build_ext_errors:
            raise BuildExtFailed()


macros = []
if sys.byteorder == "big":
    macros = [("__BIG_ENDIAN__", "1")]
else:
    macros = [("__LITTLE_ENDIAN__", "1")]


# Try to build with C extensions first, fallback to only pure-Python if building fails
try:
    kwargs = copy.deepcopy(setup_kwargs)
    kwargs["ext_modules"] = [
        Extension("ddtrace.vendor.wrapt._wrappers", sources=["ddtrace/vendor/wrapt/_wrappers.c"],),
        Extension(
            "ddtrace.vendor.msgpack._cmsgpack",
            sources=["ddtrace/vendor/msgpack/_cmsgpack.cpp"],
            libraries=libraries,
            include_dirs=["ddtrace/vendor/"],
            define_macros=macros,
        ),
    ]
    # DEV: Make sure `cmdclass` exists
    kwargs.setdefault("cmdclass", dict())
    kwargs["cmdclass"]["build_ext"] = optional_build_ext
    setup(**kwargs)
except BuildExtFailed:
    # Set `DDTRACE_BUILD_TRACE=TRUE` in CI to raise any build errors
    if os.environ.get("DDTRACE_BUILD_RAISE") == "TRUE":
        raise

    print("WARNING: Failed to install wrapt/msgpack C-extensions, using pure-Python wrapt/msgpack instead")
    setup(**setup_kwargs)
