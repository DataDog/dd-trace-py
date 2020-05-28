import os
import sys

from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand

# ORDER MATTERS
# Import this after setuptools or it will fail
from Cython.Build import cythonize  # noqa: I100
import Cython.Distutils


HERE = os.path.dirname(os.path.abspath(__file__))


def load_module_from_project_file(mod_name, fname):
    """
    Helper used to load a module from a file in this project

    DEV: Loading this way will by-pass loading all parent modules
         e.g. importing `ddtrace.vendor.psutil.setup` will load `ddtrace/__init__.py`
         which has side effects like loading the tracer
    """
    fpath = os.path.join(HERE, fname)

    if sys.version_info >= (3, 5):
        import importlib.util

        spec = importlib.util.spec_from_file_location(mod_name, fpath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    elif sys.version_info >= (3, 3):
        from importlib.machinery import SourceFileLoader

        return SourceFileLoader(mod_name, fpath).load_module()
    else:
        import imp

        return imp.load_source(mod_name, fpath)


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


def get_exts_for(name):
    try:
        mod = load_module_from_project_file(
            "ddtrace.vendor.{}.setup".format(name), "ddtrace/vendor/{}/setup.py".format(name)
        )
        return mod.get_extensions()
    except Exception as e:
        print("WARNING: Failed to load %s extensions, skipping: %s" % (name, e))
        return []


_profiling_deps = ["protobuf>=3", "intervaltree", "tenacity>=5"]


# Base `setup()` kwargs without any C-extension registering
setup(
    **dict(
        name="ddtrace",
        description="Datadog tracing code",
        url="https://github.com/DataDog/dd-trace-py",
        author="Datadog, Inc.",
        author_email="dev@datadoghq.com",
        long_description=long_description,
        long_description_content_type="text/markdown",
        license="BSD",
        packages=find_packages(exclude=["tests*"]),
        python_requires=">=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*",
        # enum34 is an enum backport for earlier versions of python
        # funcsigs backport required for vendored debtcollector
        # encoding using msgpack
        install_requires=["enum34; python_version<'3.4'", "funcsigs>=1.0.0; python_version=='2.7'", "msgpack>=0.5.0"],
        extras_require={
            # users can include opentracing by having:
            # install_requires=['ddtrace[opentracing]', ...]
            "opentracing": ["opentracing>=2.0.0"],
            # TODO: remove me when everything is updated to `profiling`
            "profile": _profiling_deps,
            "profiling": _profiling_deps,
        },
        # plugin tox
        tests_require=["tox", "flake8"],
        cmdclass={"test": Tox, "build_ext": Cython.Distutils.build_ext},
        entry_points={
            "console_scripts": [
                "ddtrace-run = ddtrace.commands.ddtrace_run:main",
                "pyddprofile = ddtrace.profiling.__main__:main",
            ]
        },
        classifiers=[
            "Programming Language :: Python",
            "Programming Language :: Python :: 2.7",
            "Programming Language :: Python :: 3.5",
            "Programming Language :: Python :: 3.6",
            "Programming Language :: Python :: 3.7",
            "Programming Language :: Python :: 3.8",
        ],
        use_scm_version=True,
        setup_requires=["setuptools_scm", "cython"],
        ext_modules=cythonize(
            [
                Cython.Distutils.Extension(
                    "ddtrace.internal._rand", sources=["ddtrace/internal/_rand.pyx"], language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector.stack",
                    sources=["ddtrace/profiling/collector/stack.pyx"],
                    language="c",
                    extra_compile_args=["-DPy_BUILD_CORE"],
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling.collector._traceback",
                    sources=["ddtrace/profiling/collector/_traceback.pyx"],
                    language="c",
                ),
                Cython.Distutils.Extension(
                    "ddtrace.profiling._build", sources=["ddtrace/profiling/_build.pyx"], language="c",
                ),
            ],
            compile_time_env={
                "PY_MAJOR_VERSION": sys.version_info.major,
                "PY_MINOR_VERSION": sys.version_info.minor,
                "PY_MICRO_VERSION": sys.version_info.micro,
            },
            force=True,
        )
        + get_exts_for("wrapt")
        + get_exts_for("psutil"),
    )
)
