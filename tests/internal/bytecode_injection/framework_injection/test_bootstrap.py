import os
from pathlib import Path
import sys

import pytest


skipif_bytecode_injection_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 10) or sys.version_info[:2] > (3, 11),
    reason="Injection is currently only supported for 3.10 and 3.11",
)

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


@skipif_bytecode_injection_not_supported
def test_bytecode_injection_smoke():
    import sys

    print(EXPL_FOLDER)
    sys.path.insert(0, str(EXPL_FOLDER))
    import tests.internal.bytecode_injection.framework_injection.preload  # noqa: F401


@skipif_bytecode_injection_not_supported
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


@skipif_bytecode_injection_not_supported
@pytest.mark.subprocess(
    env=expl_env(DD_BYTECODE_INJECTION_OUTPUT_FILE="expl.txt"),
    out=check_output_file,
)
def test_bytecode_injection_file_output():
    from pathlib import Path

    from tests.internal.bytecode_injection.framework_injection._config import config

    assert config.output_file == Path("expl.txt")
