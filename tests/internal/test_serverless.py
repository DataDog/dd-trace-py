import enum
import inspect
import os
import pathlib
from typing import NamedTuple

import pytest

from ddtrace.internal.serverless import in_aws_lambda_microvm
from ddtrace.internal.serverless import in_azure_function
from ddtrace.internal.serverless import in_gcp_function
from tests.utils import override_env


def test_is_deprecated_gcp_function():
    with override_env(dict(FUNCTION_NAME="test_function", GCP_PROJECT="project_name")):
        assert in_gcp_function() is True


def test_is_newer_gcp_function():
    with override_env(dict(K_SERVICE="test_function", FUNCTION_TARGET="function_target")):
        assert in_gcp_function() is True


def test_not_gcp_function():
    assert in_gcp_function() is False


def test_is_azure_function():
    with override_env(dict(FUNCTIONS_WORKER_RUNTIME="python", FUNCTIONS_EXTENSION_VERSION="2")):
        assert in_azure_function() is True


def test_not_azure_function():
    assert in_azure_function() is False


@pytest.mark.parametrize(
    "environment,expected",
    [
        ({"AWS_LAMBDA_MICROVM_IMAGE_ARN": "arn:aws:lambda:us-east-1:123456789012:image:test"}, True),
        ({}, False),
        ({"AWS_LAMBDA_MICROVM_IMAGE_ARN": "   "}, False),
    ],
)
def test_is_aws_lambda_microvm(environment, expected):
    with override_env(environment, replace_os_env=True):
        assert in_aws_lambda_microvm() is expected


standard_blocklist = [
    "ddtrace.appsec._api_security.api_manager",
    "ddtrace.internal.telemetry.writer",
    "email.mime.application",
    "email.mime.multipart",
    "http.client",
    "logging.handlers",
    "multiprocessing",
    "importlib_metadata",
    "ddtrace._trace.utils_botocore.span_pointers",
    "ddtrace._trace.utils_botocore.span_tags",
    # These modules must not be imported because their source files are
    # specifically removed from the serverless python layer.
    # See https://github.com/DataDog/datadog-lambda-python/blob/main/Dockerfile
    "ddtrace.appsec._iast._ast.ast_patching",
    "ddtrace.appsec._iast._ast.iastpatch",
    "ddtrace.appsec._iast._taint_tracking._native",
    "ddtrace.appsec._iast._taint_tracking._vendor",
    "ddtrace.internal.security._stacktrace",
    "ddtrace.internal.datadog.profiling.libdd_wrapper",
    "ddtrace.internal.datadog.profiling.ddup._ddup",
    "ddtrace.internal.datadog.profiling.stack._stack",
    "ddtrace.internal._file_queue",
    "secrets",
]
expanded_blocklist = (
    standard_blocklist
    + [
        # wrapt 2.0 is importing importlib.metada
        # "importlib.metadata",
    ]
)


@pytest.mark.parametrize(
    "package,blocklist",
    [
        ("ddtrace", expanded_blocklist),
        ("ddtrace.contrib.internal.aws_lambda", expanded_blocklist),
        ("ddtrace.contrib.internal.psycopg", expanded_blocklist),
        # requests imports urlib3 which imports importlib.metadata
        # TODO: Fix the requests parameter in a future PR
        ("ddtrace.contrib.internal.requests", standard_blocklist),
    ],
)
def test_slow_imports(package, blocklist, run_python_code_in_subprocess):
    # We should lazy load certain modules to avoid slowing down the startup
    # time when running in a serverless environment.  This test will fail if
    # any of those modules are imported during the import of ddtrace.
    env = os.environ.copy()
    env.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "foobar",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "False",
            "DD_API_SECURITY_ENABLED": "False",
        }
    )

    code = f"""
import sys

blocklist = {blocklist}

class BlockListFinder:
    def find_spec(self, fullname, *args):
        if fullname in blocklist:
            raise ImportError(f"module {{fullname}} was imported!")
        return None

sys.meta_path = [BlockListFinder()] + sys.meta_path

import {package}
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert stdout.decode() == ""
    assert stderr.decode() == ""
    assert status == 0


@pytest.mark.parametrize(
    "from_,import_",
    [
        ("ddtrace", "__version__"),
        ("ddtrace", "patch"),
        ("ddtrace", "patch_all"),
        ("ddtrace._trace._span_pointer", "_SpanPointerDescription"),
        ("ddtrace._trace._span_pointer", "_SpanPointerDirection"),
        ("ddtrace._trace.utils_botocore.span_pointers.dynamodb", "_aws_dynamodb_item_span_pointer_description"),
        ("ddtrace._trace.utils_botocore.span_pointers.s3", "_aws_s3_object_span_pointer_description"),
        ("ddtrace.contrib.internal.trace_utils", "_get_request_header_client_ip"),
        ("ddtrace.data_streams", "set_consume_checkpoint"),
        ("ddtrace.debugging._exception.replay", "SpanExceptionHandler"),
        ("ddtrace.debugging._uploader", "SignalUploader"),
        ("ddtrace.internal", "core"),
        ("ddtrace.internal._exceptions", "BlockingException"),
        ("ddtrace.internal.appsec.product", "start"),
        ("ddtrace.internal.sca.product", "start"),
        ("ddtrace.internal.sca.product", "post_preload"),
        ("ddtrace.internal.telemetry", "telemetry_writer"),
        ("ddtrace.internal.utils", "get_blocked"),
        ("ddtrace.internal.utils", "http"),
        ("ddtrace.llmobs", "LLMObs"),
        ("ddtrace.opentelemetry", "TracerProvider"),
        ("ddtrace.profiling", "profiler"),
        ("ddtrace.propagation.http", "HTTPPropagator"),
        ("ddtrace.trace", "Context, Span, tracer"),
        ("ddtrace.trace", "Span"),
        ("ddtrace.trace", "tracer"),
    ],
)
def test_serverless_imports(from_, import_, run_python_code_in_subprocess):
    # datadog-lambda-python imports a bunch of packages from ddtrace.  This
    # test ensures that none of those imports break.  If this test fails, then
    # either you will need to retain the import that was removed or you must
    # update datadog-lambda-python to support the new import path.
    env = os.environ.copy()
    env.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "foobar",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "False",
            "DD_API_SECURITY_ENABLED": "False",
        }
    )

    code = f"from {from_} import {import_}"

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert stdout.decode() == ""
    assert stderr.decode() == ""
    assert status == 0


# Symbols datadog-lambda-python calls or mocks, under the path it uses to reach them.
# Fingerprints come from the live object and omit annotations. A function records
# classmethod or staticmethod and its parameter list. A class records its bases, an
# __init__ defined on that class, and NamedTuple fields. An enum member records its
# value. Any other attribute records whether it is a descriptor or a plain value.
# Keep the old shape working, or update datadog-lambda-python first and then the entry.
_SERVERLESS_PRIVATE_API = [
    # datadog_lambda/asm.py
    (
        "ddtrace.contrib.internal.trace_utils",
        "_get_request_header_client_ip",
        "function (headers, peer_ip=None, headers_are_case_sensitive=False)",
    ),
    ("ddtrace.internal.core", "dispatch", "function (event_id, args=None, allow_raise=False)"),
    ("ddtrace.internal.core", "set_item", "function (data_key, data_value)"),
    ("ddtrace.internal.utils", "get_blocked", "function ()"),
    ("ddtrace.internal.utils.http", "_get_blocked_template", "function (accept_header_value, security_response_id)"),
    # datadog_lambda/span_pointers.py
    (
        "ddtrace._trace._span_pointer",
        "_SpanPointerDescription",
        "class(NamedTuple); fields pointer_kind, pointer_direction, pointer_hash, extra_attributes",
    ),
    ("ddtrace._trace._span_pointer", "_SpanPointerDirection", "class(Enum)"),
    ("ddtrace._trace._span_pointer", "_SpanPointerDirection.UPSTREAM", "enum-member 'u'"),
    (
        "ddtrace._trace.utils_botocore.span_pointers.dynamodb",
        "_aws_dynamodb_item_span_pointer_description",
        "function (operation, pointer_direction, table_name, primary_key)",
    ),
    (
        "ddtrace._trace.utils_botocore.span_pointers.s3",
        "_aws_s3_object_span_pointer_description",
        "function (operation, pointer_direction, bucket, key, etag)",
    ),
    # datadog_lambda/tracing.py
    ("ddtrace.trace", "Context._meta", "descriptor"),
    ("ddtrace.trace", "Context._metrics", "descriptor"),
    (
        "ddtrace.trace",
        "Span._add_span_pointer",
        "function (self, pointer_kind, pointer_direction, pointer_hash, extra_attributes=None)",
    ),
    # datadog_lambda/wrapper.py
    ("ddtrace.internal.appsec.product", "start", "function ()"),
    ("ddtrace.debugging._exception.replay", "SpanExceptionHandler", "class()"),
    ("ddtrace.debugging._exception.replay", "SpanExceptionHandler.enable", "classmethod (cls)"),
    (
        "ddtrace.debugging._uploader",
        "SignalUploader",
        "class(ddtrace.internal.agent.AgentCheckPeriodicService); __init__(self, interval=None)",
    ),
    ("ddtrace.debugging._uploader", "SignalUploader._instance", "attribute None"),
    ("ddtrace.debugging._uploader", "SignalUploader.periodic", "function (self)"),
]


def _call_signature(obj):
    signature = inspect.signature(obj)
    parameters = [parameter.replace(annotation=inspect.Parameter.empty) for parameter in signature.parameters.values()]
    return str(signature.replace(parameters=parameters, return_annotation=inspect.Signature.empty))


def _base_labels(cls):
    labels = []
    for base in getattr(cls, "__orig_bases__", cls.__bases__):
        name = getattr(base, "__name__", None)
        if not name or name == "object":
            continue
        module = getattr(base, "__module__", "")
        if module == "ddtrace" or module.startswith("ddtrace."):
            labels.append(f"{module}.{name}")
        else:
            labels.append(name)
    return labels


def _describe_serverless_symbol(raw):
    if isinstance(raw, classmethod):
        return "classmethod " + _call_signature(raw.__func__)
    if isinstance(raw, staticmethod):
        return "staticmethod " + _call_signature(raw.__func__)
    if isinstance(raw, property):
        return "property"
    if inspect.isdatadescriptor(raw):
        return "descriptor"
    if inspect.isclass(raw):
        parts = [f"class({', '.join(_base_labels(raw))})"]
        if issubclass(raw, enum.Enum):
            return "; ".join(parts)
        fields = getattr(raw, "_fields", None)
        if isinstance(fields, tuple):
            defaults = getattr(raw, "_field_defaults", {})
            rendered = [f"{name}={defaults[name]!r}" if name in defaults else name for name in fields]
            parts.append("fields " + ", ".join(rendered))
            return "; ".join(parts)
        init = raw.__dict__.get("__init__")
        if init is not None:
            func = init.__func__ if isinstance(init, (classmethod, staticmethod)) else init
            parts.append("__init__" + _call_signature(func))
        return "; ".join(parts)
    if isinstance(raw, enum.Enum):
        return f"enum-member {raw.value!r}"
    if callable(raw):
        return "function " + _call_signature(raw)
    if raw is None or isinstance(raw, (bool, int, float, str)):
        return f"attribute {raw!r}"
    return "attribute"


def _source_position(obj):
    try:
        _lines, start = inspect.getsourcelines(obj)
    except (OSError, TypeError):
        return None
    file = inspect.getsourcefile(obj)
    if not file:
        return None
    return pathlib.Path(file).resolve(), start


def _locate_serverless_symbol(raw, owner):
    target = raw
    if isinstance(raw, (classmethod, staticmethod)):
        target = raw.__func__
    elif isinstance(raw, property):
        target = raw.fget
    found = _source_position(target)
    if found is None and inspect.isclass(owner):
        found = _source_position(owner)
    if found is None:
        return "built-in"
    path, line = found
    import ddtrace

    root = pathlib.Path(ddtrace.__file__).resolve().parent.parent
    try:
        shown = path.relative_to(root)
    except ValueError:
        shown = pathlib.Path(path.name)
    return f"{shown}:{line}"


def _report_serverless_private_api(probes):
    import importlib

    for module_name, attr_path in probes:
        key = f"{module_name}.{attr_path}"
        try:
            current = importlib.import_module(module_name)
            raw = current
            owner = current
            parts = attr_path.split(".")
            for index, attr in enumerate(parts):
                owner = current
                raw = inspect.getattr_static(current, attr)
                # The last component is fingerprinted from the raw descriptor. Calling
                # getattr would invoke a getter such as Context._meta.
                if index < len(parts) - 1:
                    current = getattr(current, attr)
            description = _describe_serverless_symbol(raw)
            location = _locate_serverless_symbol(raw, owner)
            print(f"SERVERLESS_API\tOK\t{key}\t{description}\t{location}")
        except Exception as exc:
            message = str(exc).replace("\n", " ")
            print(f"SERVERLESS_API\tERR\t{key}\t{type(exc).__name__}: {message}")


def test_serverless_fingerprint_distinguishes_method_kinds():
    class Handler:
        def plain(self, a, b=None):
            pass

        @classmethod
        def enable(cls):
            pass

        @staticmethod
        def stat():
            pass

    assert _describe_serverless_symbol(inspect.getattr_static(Handler, "plain")) == "function (self, a, b=None)"
    assert _describe_serverless_symbol(inspect.getattr_static(Handler, "enable")) == "classmethod (cls)"
    assert _describe_serverless_symbol(inspect.getattr_static(Handler, "stat")) == "staticmethod ()"


def test_serverless_fingerprint_covers_class_shape_and_attributes():
    class Service:
        def periodic(self):
            pass

    class Uploader(Service):
        _instance = None

        def __init__(self, interval=None):
            pass

    class Direction(enum.Enum):
        UPSTREAM = "u"

    class Description(NamedTuple):
        pointer_kind: str
        extra: str = "x"

    class Box:
        @property
        def meta(self):
            return {}

        class Desc:
            def __get__(self, obj, typ=None):
                return {}

            def __set__(self, obj, value):
                pass

        metrics = Desc()

    class Empty:
        pass

    assert _describe_serverless_symbol(Uploader) == "class(Service); __init__(self, interval=None)"
    assert _describe_serverless_symbol(inspect.getattr_static(Uploader, "periodic")) == "function (self)"
    assert _describe_serverless_symbol(inspect.getattr_static(Uploader, "_instance")) == "attribute None"
    assert _describe_serverless_symbol(Direction) == "class(Enum)"
    assert _describe_serverless_symbol(inspect.getattr_static(Direction, "UPSTREAM")) == "enum-member 'u'"
    assert _describe_serverless_symbol(Description) == "class(NamedTuple); fields pointer_kind, extra='x'"
    assert _describe_serverless_symbol(inspect.getattr_static(Box, "meta")) == "property"
    assert _describe_serverless_symbol(inspect.getattr_static(Box, "metrics")) == "descriptor"
    assert _describe_serverless_symbol(Empty) == "class()"


def test_serverless_symbol_location_names_the_defining_file():
    location = _locate_serverless_symbol(_call_signature, None)
    assert location.endswith(f":{_call_signature.__code__.co_firstlineno}")
    assert "test_serverless.py" in location


def test_serverless_private_api(run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "foobar",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "False",
            "DD_API_SECURITY_ENABLED": "False",
        }
    )
    probes = [(module, attr_path) for module, attr_path, _expected in _SERVERLESS_PRIVATE_API]
    helpers = "\n\n".join(
        inspect.getsource(helper)
        for helper in (
            _call_signature,
            _base_labels,
            _describe_serverless_symbol,
            _source_position,
            _locate_serverless_symbol,
            _report_serverless_private_api,
        )
    )
    code = f"""
import enum
import importlib
import inspect
import pathlib

{helpers}

_report_serverless_private_api({probes!r})
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr.decode()
    assert stderr.decode() == ""

    reported = {}
    for line in stdout.decode().splitlines():
        if not line.startswith("SERVERLESS_API\t"):
            continue
        kind, key, payload, *rest = line.split("\t")[1:]
        reported[key] = (kind, payload, rest[0] if rest else "")

    mismatches = []
    for module, attr_path, expected in _SERVERLESS_PRIVATE_API:
        key = f"{module}.{attr_path}"
        kind, payload, location = reported.get(key, ("ERR", "no result", ""))
        if kind != "OK" or payload != expected:
            where = f"  [{location}]" if location else ""
            mismatches.append(f"{key}\n    expected: {expected}\n    actual:   {payload}{where}")
    assert mismatches == [], "\n".join(mismatches)


# Fingerprints cannot see how datadog-lambda calls the native Context constructor or
# how it reads the objects that ddtrace hands back. These subprocess checks run the
# same calls and reads that datadog_lambda/tracing.py, asm.py and wrapper.py make.
def _serverless_subprocess_env(**extra):
    env = os.environ.copy()
    env.update(
        {
            "AWS_LAMBDA_FUNCTION_NAME": "foobar",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "False",
            "DD_API_SECURITY_ENABLED": "False",
        }
    )
    env.update(extra)
    return env


def test_serverless_context_contract(run_python_code_in_subprocess):
    code = """
from ddtrace.trace import Context

trace_id = 0x0123456789ABCDEF0123456789ABCDEF
span_id = 0x0123456789ABCDEF
context = Context(
    trace_id=trace_id,
    span_id=span_id,
    sampling_priority=1.0,
    meta={"_dd.p.dm": "-0"},
    metrics={"_dd.top_level": 1.0},
)
assert context.trace_id == trace_id
assert context.span_id == span_id
assert context.sampling_priority == 1.0

copied_meta = context._meta.copy()
copied_metrics = context._metrics.copy()
assert copied_meta["_dd.p.dm"] == "-0"
assert copied_metrics["_dd.top_level"] == 1.0

context.span_id = 42
assert context.span_id == 42

Context(trace_id=1, span_id=2, sampling_priority=1)
Context(trace_id=1, span_id=2, sampling_priority=1, meta=copied_meta)
Context(trace_id=1, span_id=2, sampling_priority=1, meta=copied_meta, metrics=copied_metrics)
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=_serverless_subprocess_env())
    assert status == 0, stderr.decode()
    assert stdout.decode() == ""
    assert stderr.decode() == ""


def test_serverless_blocked_response_contract(run_python_code_in_subprocess):
    # datadog_lambda/asm.py reads the object from get_blocked() with dict-style get().
    # The listener registered by ddtrace.appsec returns a Block_config, so this one does too.
    code = """
from ddtrace.appsec._utils import Block_config
from ddtrace.internal import core
from ddtrace.internal.utils import get_blocked

assert get_blocked() is None

blocked_config = Block_config(type="none", status_code=401, security_response_id="abc123")
core.on("asm.get_blocked", lambda: blocked_config, "block_config")

blocked = get_blocked()
assert blocked
assert blocked.get("type", "auto") == "none"
assert blocked.get("content-type", "application/json") == "application/json"
assert blocked.get("block_id", "default") == "abc123"
assert blocked.get("status_code", 403) == 401
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=_serverless_subprocess_env())
    assert status == 0, stderr.decode()
    assert stdout.decode() == ""
    assert stderr.decode() == ""


def test_serverless_exception_replay_uploader_contract(run_python_code_in_subprocess):
    # datadog_lambda/wrapper.py calls SignalUploader._instance.periodic() after
    # SpanExceptionHandler.enable(). The class-body default of _instance is None, so
    # this checks the live instance that enable() creates.
    code = """
from ddtrace.debugging._exception.replay import SpanExceptionHandler
from ddtrace.debugging._uploader import SignalUploader

SpanExceptionHandler.enable()
assert isinstance(SignalUploader._instance, SignalUploader)
assert SignalUploader._instance.periodic() is None
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=_serverless_subprocess_env())
    assert status == 0, stderr.decode()
    assert stdout.decode() == ""
    assert stderr.decode() == ""
