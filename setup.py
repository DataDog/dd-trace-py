import copy
import os
import sys
import re

from distutils.command.build_ext import build_ext
from distutils.errors import CCompilerError, DistutilsExecError, DistutilsPlatformError
from setuptools import setup, find_packages, Extension
from setuptools.command.test import test as TestCommand


def get_version(package):
    """
    Return package version as listed in `__version__` in `__init__.py`.
    This method prevents to import packages at setup-time.
    """
    init_py = open(os.path.join(package, '__init__.py')).read()
    return re.search("__version__ = ['\"]([^'\"]+)['\"]", init_py).group(1)


class Tox(TestCommand):

    user_options = [('tox-args=', 'a', 'Arguments to pass to tox')]

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

# Base `setup()` kwargs without any C-extension registering
setup_kwargs = dict(
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
    extras_require={
        # users can include opentracing by having:
        # install_requires=['ddtrace[opentracing]', ...]
        'opentracing': ['opentracing>=2.0.0'],
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
        'Programming Language :: Python :: 3.7',
    ],
)


# The following from here to the end of the file is borrowed from wrapt's and msgpack's `setup.py`:
#   https://github.com/GrahamDumpleton/wrapt/blob/4ee35415a4b0d570ee6a9b3a14a6931441aeab4b/setup.py
#   https://github.com/msgpack/msgpack-python/blob/381c2eff5f8ee0b8669fd6daf1fd1ecaffe7c931/setup.py
# These helpers are useful for attempting build a C-extension and then retrying without it if it fails

libraries = []
if sys.platform == 'win32':
    libraries.append('ws2_32')
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
if sys.byteorder == 'big':
    macros = [('__BIG_ENDIAN__', '1')]
else:
    macros = [('__LITTLE_ENDIAN__', '1')]


# The following reproduces psutil's `setup.py` with minimal modifications to
# source paths and module paths
#   https://github.com/giampaolo/psutil/blob/release-5.6.2/setup.py

def _extensions_psutil():
    import contextlib
    import io
    import os
    import platform
    import sys
    import tempfile
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import setuptools
            from setuptools import setup, Extension
        except ImportError:
            setuptools = None
            from distutils.core import setup, Extension

    HERE = os.path.abspath(os.path.dirname(__file__))

    # ...so we can import _common.py
    sys.path.insert(0, os.path.join(HERE, "ddtrace/vendor/psutil"))

    from _common import AIX  # NOQA
    from _common import BSD  # NOQA
    from _common import FREEBSD  # NOQA
    from _common import LINUX  # NOQA
    from _common import MACOS  # NOQA
    from _common import NETBSD  # NOQA
    from _common import OPENBSD  # NOQA
    from _common import POSIX  # NOQA
    from _common import SUNOS  # NOQA
    from _common import WINDOWS  # NOQA


    macros = []
    if POSIX:
        macros.append(("PSUTIL_POSIX", 1))
    if BSD:
        macros.append(("PSUTIL_BSD", 1))

    sources = ['ddtrace/vendor/psutil/_psutil_common.c']
    if POSIX:
        sources.append('ddtrace/vendor/psutil/_psutil_posix.c')

    tests_require = []
    if sys.version_info[:2] <= (2, 6):
        tests_require.append('unittest2')
    if sys.version_info[:2] <= (2, 7):
        tests_require.append('mock')
    if sys.version_info[:2] <= (3, 2):
        tests_require.append('ipaddress')

    extras_require = {}
    if sys.version_info[:2] <= (3, 3):
        extras_require.update(dict(enum='enum34'))


    def get_version():
        INIT = os.path.join(HERE, 'ddtrace/vendor/psutil/__init__.py')
        with open(INIT, 'r') as f:
            for line in f:
                if line.startswith('__version__'):
                    ret = eval(line.strip().split(' = ')[1])
                    assert ret.count('.') == 2, ret
                    for num in ret.split('.'):
                        assert num.isdigit(), ret
                    return ret
            raise ValueError("couldn't find version string")


    VERSION = get_version()
    macros.append(('PSUTIL_VERSION', int(VERSION.replace('.', ''))))


    def get_description():
        README = os.path.join(HERE, 'README.rst')
        with open(README, 'r') as f:
            return f.read()


    @contextlib.contextmanager
    def silenced_output(stream_name):
        class DummyFile(io.BytesIO):
            # see: https://github.com/giampaolo/psutil/issues/678
            errors = "ignore"

            def write(self, s):
                pass

        orig = getattr(sys, stream_name)
        try:
            setattr(sys, stream_name, DummyFile())
            yield
        finally:
            setattr(sys, stream_name, orig)


    if WINDOWS:
        def get_winver():
            maj, min = sys.getwindowsversion()[0:2]
            return '0x0%s' % ((maj * 100) + min)

        if sys.getwindowsversion()[0] < 6:
            msg = "this Windows version is too old (< Windows Vista); "
            msg += "psutil 3.4.2 is the latest version which supports Windows "
            msg += "2000, XP and 2003 server"
            raise RuntimeError(msg)

        macros.append(("PSUTIL_WINDOWS", 1))
        macros.extend([
            # be nice to mingw, see:
            # http://www.mingw.org/wiki/Use_more_recent_defined_functions
            ('_WIN32_WINNT', get_winver()),
            ('_AVAIL_WINVER_', get_winver()),
            ('_CRT_SECURE_NO_WARNINGS', None),
            # see: https://github.com/giampaolo/psutil/issues/348
            ('PSAPI_VERSION', 1),
        ])

        ext = Extension(
            'ddtrace.vendor.psutil._psutil_windows',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_windows.c',
                'ddtrace/vendor/psutil/arch/windows/process_info.c',
                'ddtrace/vendor/psutil/arch/windows/process_handles.c',
                'ddtrace/vendor/psutil/arch/windows/security.c',
                'ddtrace/vendor/psutil/arch/windows/inet_ntop.c',
                'ddtrace/vendor/psutil/arch/windows/services.c',
                'ddtrace/vendor/psutil/arch/windows/global.c',
                'ddtrace/vendor/psutil/arch/windows/wmi.c',
            ],
            define_macros=macros,
            libraries=[
                "psapi", "kernel32", "advapi32", "shell32", "netapi32",
                "wtsapi32", "ws2_32", "PowrProf", "pdh",
            ],
            # extra_compile_args=["/Z7"],
            # extra_link_args=["/DEBUG"]
        )

    elif MACOS:
        macros.append(("PSUTIL_OSX", 1))
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_osx',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_osx.c',
                'ddtrace/vendor/psutil/arch/osx/process_info.c',
            ],
            define_macros=macros,
            extra_link_args=[
                '-framework', 'CoreFoundation', '-framework', 'IOKit'
            ])

    elif FREEBSD:
        macros.append(("PSUTIL_FREEBSD", 1))
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_bsd',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_bsd.c',
                'ddtrace/vendor/psutil/arch/freebsd/specific.c',
                'ddtrace/vendor/psutil/arch/freebsd/sys_socks.c',
                'ddtrace/vendor/psutil/arch/freebsd/proc_socks.c',
            ],
            define_macros=macros,
            libraries=["devstat"])

    elif OPENBSD:
        macros.append(("PSUTIL_OPENBSD", 1))
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_bsd',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_bsd.c',
                'ddtrace/vendor/psutil/arch/openbsd/specific.c',
            ],
            define_macros=macros,
            libraries=["kvm"])

    elif NETBSD:
        macros.append(("PSUTIL_NETBSD", 1))
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_bsd',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_bsd.c',
                'ddtrace/vendor/psutil/arch/netbsd/specific.c',
                'ddtrace/vendor/psutil/arch/netbsd/socks.c',
            ],
            define_macros=macros,
            libraries=["kvm"])

    elif LINUX:
        def get_ethtool_macro():
            # see: https://github.com/giampaolo/psutil/issues/659
            from distutils.unixccompiler import UnixCCompiler
            from distutils.errors import CompileError

            with tempfile.NamedTemporaryFile(
                    suffix='.c', delete=False, mode="wt") as f:
                f.write("#include <linux/ethtool.h>")

            try:
                compiler = UnixCCompiler()
                with silenced_output('stderr'):
                    with silenced_output('stdout'):
                        compiler.compile([f.name])
            except CompileError:
                return ("PSUTIL_ETHTOOL_MISSING_TYPES", 1)
            else:
                return None
            finally:
                try:
                    os.remove(f.name)
                except OSError:
                    pass

        macros.append(("PSUTIL_LINUX", 1))
        ETHTOOL_MACRO = get_ethtool_macro()
        if ETHTOOL_MACRO is not None:
            macros.append(ETHTOOL_MACRO)
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_linux',
            sources=sources + ['ddtrace/vendor/psutil/_psutil_linux.c'],
            define_macros=macros)

    elif SUNOS:
        macros.append(("PSUTIL_SUNOS", 1))
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_sunos',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_sunos.c',
                'ddtrace/vendor/psutil/arch/solaris/v10/ifaddrs.c',
                'ddtrace/vendor/psutil/arch/solaris/environ.c'
            ],
            define_macros=macros,
            libraries=['kstat', 'nsl', 'socket'])
    # AIX
    elif AIX:
        macros.append(("PSUTIL_AIX", 1))
        ext = Extension(
            'ddtrace.vendor.psutil._psutil_aix',
            sources=sources + [
                'ddtrace/vendor/psutil/_psutil_aix.c',
                'ddtrace/vendor/psutil/arch/aix/net_connections.c',
                'ddtrace/vendor/psutil/arch/aix/common.c',
                'ddtrace/vendor/psutil/arch/aix/ifaddrs.c'],
            libraries=['perfstat'],
            define_macros=macros)

    else:
        sys.exit('platform %s is not supported' % sys.platform)


    if POSIX:
        posix_extension = Extension(
            'ddtrace.vendor.psutil._psutil_posix',
            define_macros=macros,
            sources=sources)
        if SUNOS:
            posix_extension.libraries.append('socket')
            if platform.release() == '5.10':
                posix_extension.sources.append('ddtrace/vendor/psutil/arch/solaris/v10/ifaddrs.c')
                posix_extension.define_macros.append(('PSUTIL_SUNOS10', 1))
        elif AIX:
            posix_extension.sources.append('ddtrace/vendor/psutil/arch/aix/ifaddrs.c')

        extensions = [ext, posix_extension]
    else:
        extensions = [ext]


    return extensions

# Try to build with C extensions first, fallback to only pure-Python if building fails
try:
    kwargs = copy.deepcopy(setup_kwargs)
    extensions = [
        Extension(
            'ddtrace.vendor.wrapt._wrappers',
            sources=['ddtrace/vendor/wrapt/_wrappers.c'],
        ),
        Extension(
            'ddtrace.vendor.msgpack._cmsgpack',
            sources=['ddtrace/vendor/msgpack/_cmsgpack.cpp'],
            libraries=libraries,
            include_dirs=['ddtrace/vendor/'],
            define_macros=macros,
        ),
    ]
    extensions += _extensions_psutil()
    kwargs['ext_modules'] = extensions
    # DEV: Make sure `cmdclass` exists
    kwargs.setdefault('cmdclass', dict())
    kwargs['cmdclass']['build_ext'] = optional_build_ext
    setup(**kwargs)
except BuildExtFailed:
    # Set `DDTRACE_BUILD_TRACE=TRUE` in CI to raise any build errors
    if os.environ.get('DDTRACE_BUILD_RAISE') == 'TRUE':
        raise

    print('WARNING: Failed to install wrapt/msgpack C-extensions, using pure-Python wrapt/msgpack instead')
    setup(**setup_kwargs)
