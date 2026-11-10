from typing import Callable
from typing import TypeVar
from typing import cast

from wrapt import wrap_function_wrapper as _w


try:
    # DEV: We have to import setuptools first who will
    # make sure distutils is available for us.
    import setuptools  # noqa: I001

    import distutils.core as distutils_core
except ImportError:
    distutils_core = None  # type: ignore[assignment]
    setuptools = None  # type: ignore[assignment]


from ._utils import get_source_code_link


T = TypeVar("T")


def _setup(wrapped: Callable[..., T], instance: object, args: tuple[object, ...], kwargs: dict[str, object]) -> T:
    source_code_link = get_source_code_link()

    if "project_urls" not in kwargs:
        kwargs["project_urls"] = {}

    cast(dict[str, str], kwargs["project_urls"])["source_code_link"] = source_code_link

    return wrapped(*args, **kwargs)


def _patch() -> None:
    if distutils_core and setuptools:
        _w(distutils_core, "setup", _setup)
        _w(setuptools, "setup", _setup)


_patch()
