# -*- encoding: utf-8 -*-
import ast

import intervaltree


try:
    from functools import lru_cache
except ImportError:
    # This is for Python 2 but Python 2 does not use this module.
    # It's just useful for unit tests.
    def lru_cache(maxsize):
        def w(f):
            return f

        return w


try:
    # Python 2 does not have this.
    from tokenize import open as source_open
except ImportError:
    source_open = open

from ddtrace.vendor import six


def _compute_interval(node):
    min_lineno = node.lineno
    max_lineno = node.lineno
    for node in ast.walk(node):
        if hasattr(node, "lineno"):
            min_lineno = min(min_lineno, node.lineno)
            max_lineno = max(max_lineno, node.lineno)
    return (min_lineno, max_lineno + 1)


if six.PY3:
    _DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
else:
    _DEFS = (ast.FunctionDef, ast.ClassDef)


@lru_cache(maxsize=256)
def file_to_tree(filename):
    # Use tokenize.open to detect encoding
    with source_open(filename) as f:
        parsed = ast.parse(f.read(), filename=filename)
    tree = intervaltree.IntervalTree()
    for node in ast.walk(parsed):
        if isinstance(node, _DEFS):
            start, end = _compute_interval(node)
            tree[start:end] = node
    return tree


def default_def(filename, lineno):
    return filename + ":" + str(lineno)


@lru_cache(maxsize=8192)
def filename_and_lineno_to_def(filename, lineno):
    if filename[0] == "<" and filename[-1] == ">":
        return default_def(filename, lineno)

    try:
        matches = file_to_tree(filename)[lineno]
    except (IOError, OSError, SyntaxError):
        return default_def(filename, lineno)
    if matches:
        return min(matches, key=lambda i: i.length()).data.name

    return default_def(filename, lineno)
