import os
from pathlib import Path

import pytest


OUT = """Enabling bytecode injection testing
========================= Bytecode Injection Coverage ==========================

Source                                                       Lines Covered    Diff
==================================================================================
No lines found
"""


EXPL_FOLDER = Path(__file__).parent.resolve()


def expl_env(**kwargs):
    return {
        "PYTHONPATH": os.pathsep.join((str(EXPL_FOLDER), os.getenv("PYTHONPATH", ""))),
        **kwargs,
    }


def test_bytecode_injection_smoke():
    import sys

    print(EXPL_FOLDER)
    sys.path.insert(0, str(EXPL_FOLDER))
    import tests.internal.bytecode_injection.framework_injection.preload  # noqa: F401


@pytest.mark.subprocess(env=expl_env(), out=OUT)
def test_bytecode_injection_bootstrap():
    # We test that we get the expected output from the exploration debuggers
    # and no errors when running the sitecustomize.py script.
    pass


def check_output_file(o):
    assert not o

    output_file = Path("expl.txt")
    try:
        assert output_file.read_text() == OUT
        return True
    finally:
        if output_file.exists():
            output_file.unlink()


@pytest.mark.subprocess(
    env=expl_env(DD_BYTECODE_INJECTION_OUTPUT_FILE="expl.txt"),
    out=check_output_file,
)
def test_bytecode_injection_file_output():
    from pathlib import Path

    from tests.internal.bytecode_injection.framework_injection._config import config

    assert config.output_file == Path("expl.txt")
