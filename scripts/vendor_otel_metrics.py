#!/usr/bin/env python3
"""Vendor the pure-Python OpenTelemetry metrics and OTLP transport stack."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "ddtrace" / "vendor"

PACKAGES = {
    "opentelemetry-exporter-http-transport": "0.66b0",
    "opentelemetry-exporter-otlp-common": "0.66b0",
    "opentelemetry-exporter-otlp-proto-common": "1.45.0",
    "opentelemetry-exporter-otlp-proto-http": "1.45.0",
    "opentelemetry-proto": "1.45.0",
    "opentelemetry-sdk": "1.45.0",
    "opentelemetry-semantic-conventions": "0.66b0",
    "grpclib": "0.4.8",
    "h2": "4.4.1",
    "hpack": "4.2.0",
    "hyperframe": "6.1.0",
    "protobuf": "7.36.2",
}

COPIES = {
    "opentelemetry/sdk/environment_variables": "otel/sdk/environment_variables",
    "opentelemetry/sdk/metrics": "otel/sdk/metrics",
    "opentelemetry/sdk/resources": "otel/sdk/resources",
    "opentelemetry/sdk/util": "otel/sdk/util",
    "opentelemetry/sdk/version": "otel/sdk/version",
    "opentelemetry/exporter/http/transport": "otel/exporter/http/transport",
    "opentelemetry/exporter/otlp/common": "otel/exporter/otlp/common",
    "opentelemetry/exporter/otlp/proto/common/_internal/metrics_encoder": (
        "otel/exporter/otlp/proto/common/_internal/metrics_encoder"
    ),
    "opentelemetry/exporter/otlp/proto/common/version": "otel/exporter/otlp/proto/common/version",
    "opentelemetry/exporter/otlp/proto/http/_common": "otel/exporter/otlp/proto/http/_common",
    "opentelemetry/exporter/otlp/proto/http/metric_exporter": "otel/exporter/otlp/proto/http/metric_exporter",
    "opentelemetry/exporter/otlp/proto/http/version": "otel/exporter/otlp/proto/http/version",
    "h2": "h2",
    "hpack": "hpack",
    "hyperframe": "hyperframe",
}

COPY_FILES = (
    "google/protobuf/__init__.py",
    "google/protobuf/descriptor.py",
    "google/protobuf/descriptor_database.py",
    "google/protobuf/descriptor_pb2.py",
    "google/protobuf/descriptor_pool.py",
    "google/protobuf/message.py",
    "google/protobuf/message_factory.py",
    "google/protobuf/reflection.py",
    "google/protobuf/runtime_version.py",
    "google/protobuf/symbol_database.py",
    "google/protobuf/text_encoding.py",
    "google/protobuf/text_format.py",
    "google/protobuf/unknown_fields.py",
    "google/protobuf/internal/__init__.py",
    "google/protobuf/internal/api_implementation.py",
    "google/protobuf/internal/builder.py",
    "google/protobuf/internal/containers.py",
    "google/protobuf/internal/decoder.py",
    "google/protobuf/internal/encoder.py",
    "google/protobuf/internal/enum_type_wrapper.py",
    "google/protobuf/internal/extension_dict.py",
    "google/protobuf/internal/field_mask.py",
    "google/protobuf/internal/message_listener.py",
    "google/protobuf/internal/python_edition_defaults.py",
    "google/protobuf/internal/python_message.py",
    "google/protobuf/internal/type_checkers.py",
    "google/protobuf/internal/well_known_types.py",
    "google/protobuf/internal/wire_format.py",
    "opentelemetry/exporter/otlp/proto/common/__init__.py",
    "opentelemetry/exporter/otlp/proto/common/metrics_encoder.py",
    "opentelemetry/exporter/otlp/proto/common/py.typed",
    "opentelemetry/exporter/otlp/proto/common/_internal/__init__.py",
    "opentelemetry/exporter/otlp/proto/http/__init__.py",
    "opentelemetry/exporter/otlp/proto/http/py.typed",
    "opentelemetry/proto/collector/metrics/v1/metrics_service_pb2.py",
    "opentelemetry/proto/common/v1/common_pb2.py",
    "opentelemetry/proto/metrics/v1/metrics_pb2.py",
    "opentelemetry/proto/resource/v1/resource_pb2.py",
    "opentelemetry/semconv/__init__.py",
    "opentelemetry/semconv/resource/__init__.py",
    "opentelemetry/semconv/_incubating/attributes/otel_attributes.py",
    "opentelemetry/semconv/_incubating/metrics/otel_metrics.py",
    "opentelemetry/semconv/attributes/__init__.py",
    "opentelemetry/semconv/attributes/error_attributes.py",
    "opentelemetry/semconv/attributes/exception_attributes.py",
    "opentelemetry/semconv/attributes/http_attributes.py",
    "opentelemetry/semconv/attributes/server_attributes.py",
)

GRPCLIB_FILES = (
    "__init__.py",
    "_registry.py",
    "_typing.py",
    "client.py",
    "config.py",
    "const.py",
    "events.py",
    "exceptions.py",
    "metadata.py",
    "protocol.py",
    "stream.py",
    "utils.py",
    "encoding/__init__.py",
    "encoding/base.py",
    "encoding/proto.py",
)

REPLACEMENTS = (
    (r"\bopentelemetry\.exporter\b", "ddtrace.vendor.otel.exporter"),
    (r"\bopentelemetry\.proto\b", "ddtrace.vendor.otel.proto"),
    (r"\bopentelemetry\.sdk\b", "ddtrace.vendor.otel.sdk"),
    (r"\bopentelemetry\.semconv\b", "ddtrace.vendor.otel.semconv"),
    (r"\bgoogle\.protobuf\b", "ddtrace.vendor.google.protobuf"),
    (r"(?<!\.)\bh2\b", "ddtrace.vendor.h2"),
    (r"(?<!\.)\bhpack\b", "ddtrace.vendor.hpack"),
    (r"(?<!\.)\bhyperframe\b", "ddtrace.vendor.hyperframe"),
)


def _download(destination: Path) -> None:
    requirements = [f"{name}=={version}" for name, version in PACKAGES.items()]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--no-deps",
            "--dest",
            str(destination),
            *requirements,
        ],
        check=True,
    )


def _extract(wheels: Path, destination: Path) -> None:
    for wheel in wheels.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(destination)


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so", "*.pyd", "tests"),
    )


def _copy_sources(source: Path) -> None:
    for relative_source, relative_destination in COPIES.items():
        _copy_tree(source / relative_source, VENDOR / relative_destination)

    for relative_path in COPY_FILES:
        if relative_path.startswith("google/"):
            destination = VENDOR / relative_path
        else:
            destination = VENDOR / "otel" / Path(relative_path).relative_to("opentelemetry")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative_path, destination)

    for relative_path in GRPCLIB_FILES:
        src = source / "grpclib" / relative_path
        dst = VENDOR / "grpclib" / relative_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _copy_licenses(source: Path) -> None:
    destination = VENDOR / "licenses" / "otel_metrics"
    destination.mkdir(parents=True)
    for name, version in PACKAGES.items():
        normalized = name.replace("-", "_")
        candidates = list(source.glob(f"{normalized}-{version}.dist-info/licenses/*"))
        candidates += list(source.glob(f"{normalized}-{version}.dist-info/LICENSE*"))
        if not candidates:
            raise RuntimeError(f"license not found for {name} {version}")
        package_destination = destination / name
        package_destination.mkdir()
        for license_path in candidates:
            shutil.copy2(license_path, package_destination / license_path.name)


def _rewrite_imports() -> None:
    roots = (
        VENDOR / "otel",
        VENDOR / "google" / "protobuf",
        VENDOR / "urllib3",
        VENDOR / "grpclib",
        VENDOR / "h2",
        VENDOR / "hpack",
        VENDOR / "hyperframe",
    )
    for root in roots:
        for source in (*root.rglob("*.py"), *root.rglob("*.pyi")):
            text = source.read_text()
            rewritten = []
            for line in text.splitlines(keepends=True):
                if "AddSerializedFile(b" not in line:
                    for pattern, replacement in REPLACEMENTS:
                        line = re.sub(pattern, replacement, line)
                rewritten.append(line)
            source.write_text("".join(rewritten).rstrip() + "\n")

    implementation = VENDOR / "google" / "protobuf" / "internal" / "api_implementation.py"
    text = implementation.read_text()
    text = text.replace("_implementation_type = None", "_implementation_type = 'python'", 1)
    implementation.write_text(text)

    internal_encoder = VENDOR / "otel" / "exporter" / "otlp" / "proto" / "common" / "_internal" / "__init__.py"
    text = internal_encoder.read_text()
    text = text.replace(
        "from ddtrace.vendor.otel.sdk.trace import Resource",
        "from ddtrace.vendor.otel.sdk.resources import Resource",
    )
    internal_encoder.write_text(text)

    http_common = VENDOR / "otel" / "exporter" / "otlp" / "proto" / "http" / "_common" / "__init__.py"
    text = http_common.read_text()
    text = text.replace(
        "from ddtrace.vendor.otel.exporter.http.transport._urllib3 import (\n    Urllib3HTTPTransport,\n)\n",
        "from ddtrace.internal.opentelemetry.http_transport import StdlibHTTPTransport\n",
    )
    text = text.replace(
        """    return (
        RequestsHTTPTransport(verify=verify, cert=cert, session=session)
        if session
        else Urllib3HTTPTransport(verify=verify, cert=cert)
    )
""",
        """    if session:
        return RequestsHTTPTransport(verify=verify, cert=cert, session=session)
    return StdlibHTTPTransport(verify=verify, cert=cert)
""",
    )
    http_common.write_text(text)

    for name in ("client.py", "metadata.py"):
        source = VENDOR / "grpclib" / name
        source.write_text(
            source.read_text().replace("from multidict import MultiDict", "from ._multidict import MultiDict")
        )

    client = VENDOR / "grpclib" / "client.py"
    text = client.read_text()
    text = text.replace(
        """        if loop:
            warnings.warn("The loop argument is deprecated and scheduled "
                          "for removal in grpclib 0.5",
                          DeprecationWarning, stacklevel=2)

""",
        "",
    )
    client.write_text(text)


def _add_multidict_compatibility() -> None:
    source = VENDOR / "grpclib" / "_multidict.py"
    source.write_text(
        '''from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Mapping
from typing import Generic
from typing import TypeVar


_T = TypeVar("_T")


class MultiDict(Mapping[str, _T], Generic[_T]):
    """Small subset of multidict used by the vendored grpclib client."""

    def __init__(self, values=()):
        self._items = list(values.items() if isinstance(values, Mapping) else values)

    def add(self, key: str, value: _T) -> None:
        self._items.append((key, value))

    def __getitem__(self, key: str) -> _T:
        for item_key, value in reversed(self._items):
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(dict(self._items))

    def __len__(self) -> int:
        return len(dict(self._items))

    def items(self):
        return tuple(self._items)
'''
    )


def _add_namespace_files() -> None:
    for relative_path in (
        "google/__init__.py",
        "otel/__init__.py",
        "otel/exporter/__init__.py",
        "otel/exporter/http/__init__.py",
        "otel/exporter/otlp/__init__.py",
        "otel/exporter/otlp/proto/__init__.py",
        "otel/proto/__init__.py",
        "otel/proto/collector/__init__.py",
        "otel/proto/collector/metrics/__init__.py",
        "otel/proto/collector/metrics/v1/__init__.py",
        "otel/proto/common/__init__.py",
        "otel/proto/common/v1/__init__.py",
        "otel/proto/metrics/__init__.py",
        "otel/proto/metrics/v1/__init__.py",
        "otel/proto/resource/__init__.py",
        "otel/proto/resource/v1/__init__.py",
        "otel/sdk/__init__.py",
        "otel/semconv/_incubating/__init__.py",
        "otel/semconv/_incubating/attributes/__init__.py",
        "otel/semconv/_incubating/metrics/__init__.py",
    ):
        path = VENDOR / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _clean_existing() -> None:
    for name in (
        "google",
        "grpclib",
        "h2",
        "hpack",
        "hyperframe",
        "otel",
        "urllib3",
        "licenses/otel_metrics",
    ):
        shutil.rmtree(VENDOR / name, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="Use an already extracted wheel directory")
    args = parser.parse_args()

    _clean_existing()
    if args.source:
        _copy_sources(args.source)
        _copy_licenses(args.source)
    else:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            wheels = temporary / "wheels"
            extracted = temporary / "extracted"
            wheels.mkdir()
            extracted.mkdir()
            _download(wheels)
            _extract(wheels, extracted)
            _copy_sources(extracted)
            _copy_licenses(extracted)
    _add_namespace_files()
    _rewrite_imports()
    _add_multidict_compatibility()


if __name__ == "__main__":
    main()
