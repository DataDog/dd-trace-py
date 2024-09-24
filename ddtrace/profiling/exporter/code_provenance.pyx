# utilities for code provenance reporting

import gzip
import io
import json
import operator
import platform
import sysconfig
import typing

from ddtrace.internal import packages
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


if hasattr(typing, "TypedDict"):
    Package = typing.TypedDict(
        "Package",
        {
            "name": str,
            "version": str,
            "kind": typing.Literal["standard library", "library"],
            "paths": typing.List[str],
        },
    )
else:
    Package = dict  # type: ignore


stdlib_path = sysconfig.get_path("stdlib")
platstdlib_path = sysconfig.get_path("platstdlib")
purelib_path = sysconfig.get_path("purelib")
platlib_path = sysconfig.get_path("platlib")


STDLIB = []  # type: typing.List[Package]


if stdlib_path is not None:
    STDLIB.append(
        Package(
            {
                "name": "stdlib",
                "kind": "standard library",
                "version": platform.python_version(),
                "paths": [stdlib_path],
            }
        )
    )

if purelib_path is not None:
    # No library should end up here, include it just in case
    STDLIB.append(
        Package(
            {
                "name": "<unknown>",
                "kind": "library",
                "version": "<unknown>",
                "paths": [purelib_path]
                + ([] if platlib_path is None or purelib_path == platlib_path else [platlib_path]),
            }
        )
    )


if platstdlib_path is not None and platstdlib_path != stdlib_path:
    STDLIB.append(
        Package(
            {
                "name": "platstdlib",
                "kind": "standard library",
                "version": platform.python_version(),
                "paths": [platstdlib_path],
            }
        )
    )


def groupby(collection, key):
    groups = {}

    for item in collection:
        try:
            groups.setdefault(key(item), []).append(item)
        except Exception:
            log.warning("Failed to group item %r", item, exc_info=True)

    return groups.items()


_ITEMGETTER_ZERO = operator.itemgetter(0)


def build_libraries(filenames: typing.List[str]) -> typing.List[Package]:
    return [
        Package(
            {
                "name": lib.name,
                "kind": "library",
                "version": lib.version,
                "paths": [lib_and_filename[1] for lib_and_filename in libs_and_filenames],
            }
        )
        for lib, libs_and_filenames in groupby(
            {
                _
                for _ in (
                    (packages.filename_to_package(filename), filename) for filename in filenames
                )
                if _[0] is not None
            },
            _ITEMGETTER_ZERO,
        )
    ] + STDLIB

def serialize_to_compressed_bytes(libs: typing.List[Package]) -> bytes:
    code_provenance = io.BytesIO()
    with gzip.GzipFile(fileobj=code_provenance, mode="wb") as gz:
        gz.write(
            json.dumps(
                {
                    "v1": libs,
                }
            )
        )
    return code_provenance.getvalue()
