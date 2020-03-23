import os

import pytest

from ddtrace.vendor import six

from ddtrace.profiling import _line2def


def test_filename_and_lineno_to_def():
    filename = os.path.join(os.path.dirname(__file__), "_test_line2def_1.py")
    assert _line2def.filename_and_lineno_to_def(filename, 1).endswith("/_test_line2def_1.py:1")
    assert _line2def.filename_and_lineno_to_def(filename, 2).endswith("/_test_line2def_1.py:2")
    assert _line2def.filename_and_lineno_to_def(filename, 3).endswith("/_test_line2def_1.py:3")
    assert _line2def.filename_and_lineno_to_def(filename, 4).endswith("/_test_line2def_1.py:4")
    assert _line2def.filename_and_lineno_to_def(filename, 5) == "A"
    assert _line2def.filename_and_lineno_to_def(filename, 6) == "x"
    assert _line2def.filename_and_lineno_to_def(filename, 7) == "x"
    assert _line2def.filename_and_lineno_to_def(filename, 8).endswith("/_test_line2def_1.py:8")


def test_filename_and_lineno_to_def_ast():
    filename = os.path.join(os.path.dirname(__file__), "_ast_test_file.py")
    for i in range(29):
        assert _line2def.filename_and_lineno_to_def(filename, i).endswith("/_ast_test_file.py:%d" % i)
    for i in range(30, 36):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "parse"
    for i in range(36, 38):
        assert _line2def.filename_and_lineno_to_def(filename, i).endswith("/_ast_test_file.py:%d" % i)
    for i in range(38, 49):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "literal_eval"
    for i in range(49, 56):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "_convert_num"
    for i in range(56, 64):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "_convert_signed_num"
    for i in range(64, 91):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "_convert"
    for i in range(91, 92):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "literal_eval"
    for i in range(92, 94):
        assert _line2def.filename_and_lineno_to_def(filename, i).endswith("/_ast_test_file.py:%d" % i)
    for i in range(94, 103):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "dump"
    for i in range(103, 119):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "_format"
    for i in range(119, 122):
        assert _line2def.filename_and_lineno_to_def(filename, i) == "dump"


@pytest.mark.skipif(six.PY2, reason="Does not work on Python2")
def test_filename_and_lineno_to_def_async():
    filename = os.path.join(os.path.dirname(__file__), "_test_line2def_async.py")
    assert _line2def.filename_and_lineno_to_def(filename, 1).endswith("/_test_line2def_async.py:1")
    assert _line2def.filename_and_lineno_to_def(filename, 2).endswith("/_test_line2def_async.py:2")
    assert _line2def.filename_and_lineno_to_def(filename, 3).endswith("/_test_line2def_async.py:3")
    assert _line2def.filename_and_lineno_to_def(filename, 4).endswith("/_test_line2def_async.py:4")
    assert _line2def.filename_and_lineno_to_def(filename, 5) == "A"
    assert _line2def.filename_and_lineno_to_def(filename, 6) == "x"
    assert _line2def.filename_and_lineno_to_def(filename, 7) == "x"
    assert _line2def.filename_and_lineno_to_def(filename, 8).endswith("/_test_line2def_async.py:8")


def test_filename_and_lineno_to_def_oserror():
    assert _line2def.filename_and_lineno_to_def("/nonexistent", 8) == "/nonexistent:8"


def test_filename_and_lineno_to_def_wrong_syntax():
    filename = os.path.join(os.path.dirname(__file__), "_wrong_file")
    assert _line2def.filename_and_lineno_to_def(filename, 8) == ("%s:8" % filename)
