#!/usr/bin/env python3

import ast
import codecs
import os
import re
from sys import builtin_module_names
from types import ModuleType
from typing import Optional
from typing import Text
from typing import Tuple

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._python_info.stdlib import _stdlib_for_python_version
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import origin

from .visitor import AstVisitor


_VISITOR = AstVisitor()


# Prefixes for modules where IAST patching is allowed
IAST_ALLOWLIST: Tuple[Text, ...] = ("tests.appsec.iast",)
IAST_DENYLIST: Tuple[Text, ...] = (
    "flask",
    "werkzeug",
    "crypto",  # This module is patched by the IAST patch methods, propagation is not needed
    "deprecated",
    "api_pb2",  # Patching crashes with these auto-generated modules, propagation is not needed
    "api_pb2_grpc",  # ditto
    "asyncpg.pgproto",
    "blinker",
    "bytecode",
    "cattrs",
    "click",
    "ddsketch",
    "ddtrace",
    "encodings",  # this package is used to load encodings when a module is imported, propagation is not needed
    "encodings.idna",
    "envier",
    "exceptiongroup",
    "freezegun",  # Testing utilities for time manipulation
    "hypothesis",
    "importlib_metadata",
    "inspect",  # this package is used to get the stack frames, propagation is not needed
    "itsdangerous",
    "moto",  # used for mocking AWS, propagation is not needed
    "moto[all]",
    "moto[ec2]",
    "moto[s3]",
    "opentelemetry-api",
    "packaging",
    "pip",
    "pkg_resources",
    "pluggy",
    "protobuf",
    "pycparser",  # this package is called when a module is imported, propagation is not needed
    "pytest",  # Testing framework
    "setuptools",
    "sklearn",  # Machine learning library
    "tomli",
    "typing_extensions",
    "unittest.mock",
    "uvloop",
    "urlpatterns_reverse.tests",  # assertRaises eat exceptions in native code, so we don't call the original function
    "wrapt",
    "zipp",
    ## This is a workaround for Sanic failures:
    "websocket",
    "h11",
    "aioquic",
    "httptools",
    "sniffio",
    "py",
    "sanic",
    "rich",
    "httpx",
    "websockets",
    "uvicorn",
    "anyio",
    "httpcore",
)


if IAST.PATCH_MODULES in os.environ:
    IAST_ALLOWLIST += tuple(os.environ[IAST.PATCH_MODULES].split(IAST.SEP_MODULES))

if IAST.DENY_MODULES in os.environ:
    IAST_DENYLIST += tuple(os.environ[IAST.DENY_MODULES].split(IAST.SEP_MODULES))


ENCODING = ""

log = get_logger(__name__)


def get_encoding(module_path: Text) -> Text:
    """
    First tries to detect the encoding for the file,
    otherwise, returns global encoding default
    """
    global ENCODING
    if not ENCODING:
        try:
            ENCODING = codecs.lookup("utf-8-sig").name
        except LookupError:
            ENCODING = codecs.lookup("utf-8").name
    return ENCODING


_NOT_PATCH_MODULE_NAMES = _stdlib_for_python_version() | set(builtin_module_names)


def _in_python_stdlib(module_name: str) -> bool:
    return module_name.split(".")[0].lower() in [x.lower() for x in _NOT_PATCH_MODULE_NAMES]


def _should_iast_patch(module_name: Text) -> bool:
    """
    select if module_name should be patch from the longuest prefix that match in allow or deny list.
    if a prefix is in both list, deny is selected.
    """
    # TODO: A better solution would be to migrate the original algorithm to C++:
    # max_allow = max((len(prefix) for prefix in IAST_ALLOWLIST if module_name.startswith(prefix)), default=-1)
    # max_deny = max((len(prefix) for prefix in IAST_DENYLIST if module_name.startswith(prefix)), default=-1)
    # diff = max_allow - max_deny
    # return diff > 0 or (diff == 0 and not _in_python_stdlib_or_third_party(module_name))
    if module_name.lower().startswith(IAST_ALLOWLIST):
        return True
    if module_name.lower().startswith(IAST_DENYLIST):
        return False
    return not _in_python_stdlib(module_name)


def visit_ast(
    source_text: Text,
    module_path: Text,
    module_name: Text = "",
) -> Optional[str]:
    parsed_ast = ast.parse(source_text, module_path)

    _VISITOR.update_location(filename=module_path, module_name=module_name)
    modified_ast = _VISITOR.visit(parsed_ast)

    if not _VISITOR.ast_modified:
        return None

    ast.fix_missing_locations(modified_ast)
    return modified_ast


_FLASK_INSTANCE_REGEXP = re.compile(r"(\S*)\s*=.*Flask\(.*")


def _remove_flask_run(text: Text) -> Text:
    """
    Find and remove flask app.run() call. This is used for patching
    the app.py file and exec'ing to replace the module without creating
    a new instance.
    """
    flask_instance_name = re.search(_FLASK_INSTANCE_REGEXP, text)
    if not flask_instance_name:
        return text
    groups = flask_instance_name.groups()
    if not groups:
        return text

    instance_name = groups[-1]
    new_text = re.sub(instance_name + r"\.run\(.*\)", "pass", text)
    return new_text


def astpatch_module(module: ModuleType, remove_flask_run: bool = False) -> Tuple[str, str]:
    module_name = module.__name__

    module_origin = origin(module)
    if module_origin is None:
        log.debug("astpatch_source couldn't find the module: %s", module_name)
        return "", ""

    module_path = str(module_origin)
    try:
        if module_origin.stat().st_size == 0:
            # Don't patch empty files like __init__.py
            log.debug("empty file: %s", module_path)
            return "", ""
    except OSError:
        log.debug("astpatch_source couldn't find the file: %s", module_path, exc_info=True)
        return "", ""

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        log.debug("extension not supported: %s for: %s", module_ext, module_path)
        return "", ""

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            log.debug("unicode decode error for file: %s", module_path, exc_info=True)
            return "", ""

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        log.debug("empty file: %s", module_path)
        return "", ""

    if remove_flask_run:
        source_text = _remove_flask_run(source_text)

    new_source = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if new_source is None:
        log.debug("file not ast patched: %s", module_path)
        return "", ""

    return module_path, new_source
