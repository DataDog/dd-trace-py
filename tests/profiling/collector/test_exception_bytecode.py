"""Unit tests for _find_except_bytecode_indexes_3_10 and _find_except_bytecode_indexes_3_11.

Each test compiles a small function and asserts the exact bytecode offsets where
the finder detects except-block injection points. Offsets are version-specific
since 3.10 and 3.11 emit different bytecode. The docstrings include the
disassembly for both versions so the expected offsets can be verified by inspection.
"""

import sys
import textwrap

import pytest

from ddtrace.profiling.collector._exception_bytecode import _find_except_bytecode_indexes_3_10
from ddtrace.profiling.collector._exception_bytecode import _find_except_bytecode_indexes_3_11


py_version = sys.version_info[:2]

pytestmark = pytest.mark.skipif(
    py_version not in ((3, 10), (3, 11)),
    reason="Bytecode offset finder tests only run on Python 3.10 and 3.11",
)

if py_version == (3, 10):
    _find_offsets = _find_except_bytecode_indexes_3_10
elif py_version == (3, 11):
    _find_offsets = _find_except_bytecode_indexes_3_11
else:
    _find_offsets = None

# Expected offsets keyed by (major, minor)
_EXPECTED = {
    "single_bare_except": {(3, 10): [14], (3, 11): [12]},
    "single_typed_except": {(3, 10): [20], (3, 11): [28]},
    "chained_excepts": {(3, 10): [20, 38], (3, 11): [28, 52]},
    "chained_excepts_with_bare": {(3, 10): [20, 38, 50], (3, 11): [28, 52, 60]},
    "nested_try_except": {(3, 10): [24, 46], (3, 11): [30, 64]},
    "no_except": {(3, 10): [], (3, 11): []},
    "try_finally_no_except": {(3, 10): [], (3, 11): []},
    "except_as_variable": {(3, 10): [22], (3, 11): [28]},
    "multiple_separate_try_blocks": {(3, 10): [18, 42, 62], (3, 11): [26, 62, 84]},
}


def _compile_func(src: str):
    """Compile a function from source and return its code object."""
    src = textwrap.dedent(src)
    ns = {}
    exec(compile(src, "<test>", "exec"), ns)
    for v in ns.values():
        if callable(v) and hasattr(v, "__code__"):
            return v.__code__
    raise RuntimeError("No function found in source")


def test_bytecode_finder_single_bare_except():
    """A bare ``except:`` block.

    3.10 bytecode (expected offset: [14]):
      3           0 SETUP_FINALLY            3 (to 8)
      4           2 POP_BLOCK
                  4 LOAD_CONST               0 (None)
                  6 RETURN_VALUE
      5     >>    8 POP_TOP
                 10 POP_TOP
                 12 POP_TOP
      6    -->  14 POP_EXCEPT               <-- injection point
                 16 LOAD_CONST               0 (None)
                 18 RETURN_VALUE

    3.11 bytecode (expected offset: [12]):
      3           2 NOP
      4           4 LOAD_CONST               0 (None)
                  6 RETURN_VALUE
                  8 PUSH_EXC_INFO
      5          10 POP_TOP
      6    -->  12 POP_EXCEPT               <-- injection point
                 14 LOAD_CONST               0 (None)
                 16 RETURN_VALUE
    """
    code = _compile_func("""
        def f():
            try:
                pass
            except:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["single_bare_except"][py_version]


def test_bytecode_finder_single_typed_except():
    """A single ``except ValueError:`` block.

    3.10 bytecode (expected offset: [20]):
      3           0 SETUP_FINALLY            3 (to 8)
      4           2 POP_BLOCK
                  4 LOAD_CONST               0 (None)
                  6 RETURN_VALUE
      5     >>    8 DUP_TOP
                 10 LOAD_GLOBAL              0 (ValueError)
                 12 JUMP_IF_NOT_EXC_MATCH    13 (to 26)
                 14 POP_TOP
                 16 POP_TOP
                 18 POP_TOP
      6    -->  20 POP_EXCEPT               <-- injection point
                 22 LOAD_CONST               0 (None)
                 24 RETURN_VALUE
      5     >>   26 RERAISE                  0

    3.11 bytecode (expected offset: [28]):
      3           2 NOP
      4           4 LOAD_CONST               0 (None)
                  6 RETURN_VALUE
                  8 PUSH_EXC_INFO
      5          10 LOAD_GLOBAL              0 (ValueError)
                 22 CHECK_EXC_MATCH
                 24 POP_JUMP_FORWARD_IF_FALSE     4 (to 34)
                 26 POP_TOP
      6    -->  28 POP_EXCEPT               <-- injection point
                 30 LOAD_CONST               0 (None)
                 32 RETURN_VALUE
      5     >>   34 RERAISE                  0
    """
    code = _compile_func("""
        def f():
            try:
                pass
            except ValueError:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["single_typed_except"][py_version]


def test_bytecode_finder_chained_excepts():
    """Two typed except clauses in the same try block.

    3.10 bytecode (expected offsets: [20, 38]):
      5     >>    8 DUP_TOP / LOAD_GLOBAL (ValueError) / JUMP_IF_NOT_EXC_MATCH
                 14-18 POP_TOP x3
      6    -->  20 POP_EXCEPT               <-- injection point 1
      7     >>   26 DUP_TOP / LOAD_GLOBAL (TypeError) / JUMP_IF_NOT_EXC_MATCH
                 32-36 POP_TOP x3
      8    -->  38 POP_EXCEPT               <-- injection point 2

    3.11 bytecode (expected offsets: [28, 52]):
      5          10 LOAD_GLOBAL (ValueError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE
                 26 POP_TOP
      6    -->  28 POP_EXCEPT               <-- injection point 1
      7     >>   34 LOAD_GLOBAL (TypeError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE
                 50 POP_TOP
      8    -->  52 POP_EXCEPT               <-- injection point 2
    """
    code = _compile_func("""
        def f():
            try:
                pass
            except ValueError:
                pass
            except TypeError:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["chained_excepts"][py_version]


def test_bytecode_finder_chained_excepts_with_bare():
    """Two typed excepts followed by a bare ``except:`` in the same try block.

    The bare except shares the PUSH_EXC_INFO with the typed handlers and is reached
    via fallthrough from the last POP_JUMP_FORWARD_IF_FALSE. The finder detects it
    by checking the jump target: if it lands on POP_TOP (not LOAD_GLOBAL or RERAISE),
    it's a bare except handler.

    3.10 bytecode (expected offsets: [20, 38, 50]):
      5     >>    8 DUP_TOP / LOAD_GLOBAL (ValueError) / JUMP_IF_NOT_EXC_MATCH
      6    -->  20 POP_EXCEPT               <-- injection point 1
      7     >>   26 DUP_TOP / LOAD_GLOBAL (TypeError) / JUMP_IF_NOT_EXC_MATCH
      8    -->  38 POP_EXCEPT               <-- injection point 2
      9     >>   44 POP_TOP / POP_TOP / POP_TOP
     10    -->  50 POP_EXCEPT               <-- injection point 3

    3.11 bytecode (expected offsets: [28, 52, 60]):
      5          10 LOAD_GLOBAL (ValueError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE (to 34)
      6    -->  28 POP_EXCEPT               <-- injection point 1
      7     >>   34 LOAD_GLOBAL (TypeError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE (to 58)
      8    -->  52 POP_EXCEPT               <-- injection point 2
      9     >>   58 POP_TOP                  <-- bare except (jump target is POP_TOP)
     10    -->  60 POP_EXCEPT               <-- injection point 3
    """
    code = _compile_func("""
        def f():
            try:
                pass
            except ValueError:
                pass
            except TypeError:
                pass
            except:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["chained_excepts_with_bare"][py_version]


def test_bytecode_finder_nested_try_except():
    """Nested try/except blocks — each should produce its own injection point.

    3.10 bytecode (expected offsets: [24, 46]):
      3           0 SETUP_FINALLY           16 (to 34)
      4           2 SETUP_FINALLY            4 (to 12)
      6     >>   12 DUP_TOP / LOAD_GLOBAL (ValueError) / JUMP_IF_NOT_EXC_MATCH
      7    -->  24 POP_EXCEPT               <-- injection point 1 (inner)
      8     >>   34 DUP_TOP / LOAD_GLOBAL (RuntimeError) / JUMP_IF_NOT_EXC_MATCH
      9    -->  46 POP_EXCEPT               <-- injection point 2 (outer)

    3.11 bytecode (expected offsets: [30, 64]):
      6          12 LOAD_GLOBAL (ValueError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE
      7    -->  30 POP_EXCEPT               <-- injection point 1 (inner)
      8     >>   44 PUSH_EXC_INFO
                 46 LOAD_GLOBAL (RuntimeError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE
      9    -->  64 POP_EXCEPT               <-- injection point 2 (outer)
    """
    code = _compile_func("""
        def f():
            try:
                try:
                    pass
                except ValueError:
                    pass
            except RuntimeError:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["nested_try_except"][py_version]


def test_bytecode_finder_no_except():
    """A function with no try/except — should produce zero injection points.

    3.10 bytecode:
      3           0 LOAD_CONST               1 (3)
                  2 STORE_FAST               0 (x)
      4           4 LOAD_FAST                0 (x)
                  6 RETURN_VALUE

    3.11 bytecode:
      3           2 LOAD_CONST               1 (3)
                  4 STORE_FAST               0 (x)
      4           6 LOAD_FAST                0 (x)
                  8 RETURN_VALUE
    """
    code = _compile_func("""
        def f():
            x = 1 + 2
            return x
    """)
    assert _find_offsets(code) == _EXPECTED["no_except"][py_version]


def test_bytecode_finder_try_finally_no_except():
    """try/finally without except — should produce zero injection points.

    3.10 bytecode:
      3           0 SETUP_FINALLY            3 (to 8)
      4           2 POP_BLOCK
      6           4 LOAD_CONST               0 (None)
                  6 RETURN_VALUE
            >>    8 RERAISE                  0

    3.11 bytecode:
      3           2 NOP
      4           4 NOP
      6           6 LOAD_CONST               0 (None)
                  8 RETURN_VALUE
                 10 PUSH_EXC_INFO
                 12 RERAISE                  0
    """
    code = _compile_func("""
        def f():
            try:
                pass
            finally:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["try_finally_no_except"][py_version]


def test_bytecode_finder_except_as_variable():
    """``except ValueError as e:`` — the ``as`` binding uses STORE_FAST + SETUP_FINALLY.

    3.10 bytecode (expected offset: [22]):
      5     >>    8 DUP_TOP
                 10 LOAD_GLOBAL              0 (ValueError)
                 12 JUMP_IF_NOT_EXC_MATCH    24 (to 48)
                 14 POP_TOP
                 16 STORE_FAST               0 (e)
                 18 POP_TOP
                 20 SETUP_FINALLY            9 (to 40)
      6    -->  22 LOAD_FAST                0 (e)   <-- injection point
                 24 STORE_FAST               1 (_)

    3.11 bytecode (expected offset: [28]):
      5          10 LOAD_GLOBAL              0 (ValueError)
                 22 CHECK_EXC_MATCH
                 24 POP_JUMP_FORWARD_IF_FALSE    13 (to 52)
                 26 STORE_FAST               0 (e)
      6    -->  28 LOAD_FAST                0 (e)   <-- injection point
                 30 STORE_FAST               1 (_)
    """
    code = _compile_func("""
        def f():
            try:
                pass
            except ValueError as e:
                _ = e
    """)
    assert _find_offsets(code) == _EXPECTED["except_as_variable"][py_version]


def test_bytecode_finder_multiple_separate_try_blocks():
    """Three independent try/except blocks in the same function.

    3.10 bytecode (expected offsets: [18, 42, 62]):
      5     >>    6 DUP_TOP / LOAD_GLOBAL (ValueError) / JUMP_IF_NOT_EXC_MATCH
      6    -->  18 POP_EXCEPT               <-- injection point 1
      9     >>   30 DUP_TOP / LOAD_GLOBAL (TypeError) / JUMP_IF_NOT_EXC_MATCH
     10    -->  42 POP_EXCEPT               <-- injection point 2
     13    >>   56 POP_TOP / POP_TOP / POP_TOP
     14    -->  62 POP_EXCEPT               <-- injection point 3

    3.11 bytecode (expected offsets: [26, 62, 84]):
      5           8 LOAD_GLOBAL (ValueError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE
      6    -->  26 POP_EXCEPT               <-- injection point 1
      9          44 LOAD_GLOBAL (TypeError) / CHECK_EXC_MATCH / POP_JUMP_FORWARD_IF_FALSE
     10    -->  62 POP_EXCEPT               <-- injection point 2
     13          80 PUSH_EXC_INFO / POP_TOP
     14    -->  84 POP_EXCEPT               <-- injection point 3
    """
    code = _compile_func("""
        def f():
            try:
                pass
            except ValueError:
                pass
            try:
                pass
            except TypeError:
                pass
            try:
                pass
            except:
                pass
    """)
    assert _find_offsets(code) == _EXPECTED["multiple_separate_try_blocks"][py_version]
