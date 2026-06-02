import sys

import pytest

from ddtrace.internal.assembly import Assembly


@pytest.mark.skipif(sys.version_info[:2] != (3, 11), reason="targets CPython 3.11 bytecode")
def test_assembly_try_block():
    asm = Assembly()
    asm.parse(
        """
            resume          0

        start:
        try @reraise lasti
            push_null
            load_const      print
            load_const      "hello world"
            precall         1
            call            1
            return_value
        tried

        reraise:
            reraise         1
        """
    )

    asm.dis()

    exec(asm.compile())


@pytest.mark.skipif(sys.version_info[:2] != (3, 11), reason="targets CPython 3.11 bytecode")
def test_assembly_lazy_opargs():
    asm = Assembly()
    asm.parse(
        r"""
            resume          0

        start:
        try @reraise lasti
            push_null
            load_const      print
            load_const      {text}
            precall         1
            call            1
            return_value
        tried

        reraise:
            reraise         1
        """
    )

    asm.dis()

    exec(asm.compile(dict(text="hello world")))
    exec(asm.compile(dict(text=asm)))
