#!/usr/bin/env python3

import ast
import codecs
import os
import pkgutil
from typing import Tuple

import chardet


ENCODING = ""


def get_encoding(module_path):  # type: (str) -> str
    """
    First tries to detect the encoding for the file,
    otherwise, returns global encoding default
    """
    try:
        res = chardet.detect(module_path)
        return res["encoding"]
    except TypeError:
        pass

    global ENCODING
    if not ENCODING:
        try:
            ENCODING = codecs.lookup("utf-8-sig").name
        except LookupError:
            ENCODING = codecs.lookup("utf-8").name
    return ENCODING


def visit_ast(
    source_text,  # type: str
    module_path,  # type: str
    module_name="",  # type: str
):  # type: (...) -> str
    parsed_ast = ast.parse(source_text, module_path)

    try:
        from ddtrace.appsec.iast._ast.visitor import AstVisitor

        visitor = AstVisitor(
            filename=module_path,
            module_name=module_name,
        )
        modified_ast = visitor.visit(parsed_ast)

        if not visitor.ast_modified:
            return ""

    except Exception:
        raise

    return modified_ast


def astpatch_source(
    module_path="",  # type: str
    module_name="",  # type: str
):  # type: (...) -> Tuple[str, str]

    if not module_path and not module_name:
        raise Exception("Implementation Error: You must pass module_name and, optionally, module_path")

    if not module_path:
        # Get the module path from the module name (foo.bar -> foo/bar.py)
        loader = pkgutil.get_loader(module_name)

        assert loader
        if hasattr(loader, "module_spec"):
            module_path = loader.module_spec.origin
        else:
            # Enter in this else if the loader is instance of BuiltinImporter but
            # isinstance(loader, BuiltinImporter) doesn't work
            return "", ""

    if not module_path:
        return "", ""

    # Get the file extension, if it's dll, os, pyd, dyn, dynlib: return
    # If its pyc or pyo, change to .py and check that the file exists. If not,
    # return with warning.
    _, module_ext = os.path.splitext(module_path)

    if module_ext not in {".pyo", ".pyc", ".pyw", ".py"}:
        # Probably native or built-in module
        return "", ""

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
        try:
            source_text = source_file.read()
        except UnicodeDecodeError:
            return "", ""

    if os.stat(module_path).st_size == 0:
        # Don't patch empty files like __init__.py
        return "", ""

    if len(source_text.strip()) == 0:
        # Don't patch empty files like __init__.py
        return "", ""

    new_source = visit_ast(
        source_text,
        module_path,
        module_name=module_name,
    )
    if not new_source:
        return "", ""

    return module_path, new_source
