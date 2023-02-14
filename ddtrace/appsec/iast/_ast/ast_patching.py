#!/usr/bin/env python3

import ast
import codecs
import os
from types import ModuleType
from typing import Optional
from typing import Tuple

from ddtrace.appsec.iast._ast.visitor import AstVisitor
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


# Prefixes for modules where IAST patching is allowed
IAST_ALLOWLIST = ("tests.appsec.iast",)

ENCODING = ""

log = get_logger(__name__)


def get_encoding(module_path):  # type: (str) -> str
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


def _should_iast_patch(module_name):
    return not module_name.startswith("ddtrace") and module_name.startswith(IAST_ALLOWLIST)


def visit_ast(
    source_text,  # type: str
    module_path,  # type: str
    module_name="",  # type: str
):  # type: (...) -> Optional[str]
    parsed_ast = ast.parse(source_text, module_path)

    visitor = AstVisitor(
        filename=module_path,
        module_name=module_name,
    )
    modified_ast = visitor.visit(parsed_ast)

    if not visitor.ast_modified:
        return None

    return modified_ast


def astpatch_module(module):  # type: (ModuleType) -> Tuple[str, str]
    return astpatch_source(module.__name__, module.origin)


def astpatch_source(
    module_name,  # type: str
    module_path,  # type: Optional[str]
):  # type: (...) -> Tuple[str, str]
    if not module_path and not module_name:
        log.debug("astpatch_source called with no module path or name")
        return "", ""

    detected_module_path = module_path
    if not detected_module_path:
        # Get the module path from the module name (foo.bar -> foo/bar.py)
        module_watchdog = ModuleWatchdog._instance

        assert module_watchdog
        detected_module_spec = module_watchdog.find_spec(module_name)
        if detected_module_spec and hasattr(detected_module_spec, "origin"):
            detected_module_path = detected_module_spec.origin
            if not detected_module_path:
                log.debug("astpatch_source couldn't get module spec origin for: %s", module_name)
                return "", ""
        else:
            log.debug("astpatch_source: ModuleWatchdog couldn't get module spec for: %s", module_name)
            return "", ""

    assert detected_module_path
    try:
        if os.stat(detected_module_path).st_size == 0:
            # Don't patch empty files like __init__.py
            log.debug("empty file: %s", detected_module_path)
            return "", ""
    except OSError:
        log.debug("astpatch_source couldn't find the file: %s", detected_module_path, exc_info=True)
        return "", ""

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(detected_module_path)

    if module_ext.lower() not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        log.debug("extension not supported: %s for: %s", module_ext, detected_module_path)
        return "", ""

    with open(detected_module_path, "r", encoding=get_encoding(detected_module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            log.debug("unicode decode error for file: %s", detected_module_path, exc_info=True)
            return "", ""

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        log.debug("empty file: %s", detected_module_path)
        return "", ""

    new_source = visit_ast(
        source_text,
        detected_module_path,
        module_name=module_name,
    )
    if new_source is None:
        log.debug("file not ast patched: %s", detected_module_path)
        return "", ""

    return detected_module_path, new_source
