from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


try:
    import distutils.core as distutils_core

    import setuptools
except ImportError:
    distutils_core = None  # type: ignore[assignment]
    setuptools = None  # type: ignore[assignment]


from ._utils import get_source_code_link


def _setup(wrapped, instance, args, kwargs):
    source_code_link = get_source_code_link()

    if "project_urls" not in kwargs:
        kwargs["project_urls"] = {}

    kwargs["project_urls"]["source_code_link"] = source_code_link

    return wrapped(*args, **kwargs)


def _patch():

    if distutils_core and setuptools:
        _w(distutils_core, "setup", _setup)
        _w(setuptools, "setup", _setup)


_patch()
