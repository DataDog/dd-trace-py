import os
from pathlib import Path

import pytest


NEEDLE = "Enabling debugging exploration testing"

EXPL_FOLDER = Path(__file__).parent.resolve()


def expl_env(**kwargs):
    return {
        "PYTHONPATH": os.pathsep.join((str(EXPL_FOLDER), os.getenv("PYTHONPATH", ""))),
        **kwargs,
    }


@pytest.mark.subprocess(env=expl_env(), out=lambda _: NEEDLE in _, status=2)
def test_exploration_bootstrap():
    # We test that we get the expected output from the exploration debuggers
    # and no errors when running the sitecustomize.py script.
    pass


def check_output_file(o):
    assert not o

    output_file = Path("expl.txt")
    try:
        assert NEEDLE in output_file.read_text()
        return True
    finally:
        if output_file.exists():
            output_file.unlink()


@pytest.mark.subprocess(
    env=expl_env(DD_DEBUGGER_EXPL_OUTPUT_FILE="expl.txt"),
    out=check_output_file,
    status=2,
)
def test_exploration_file_output():
    from pathlib import Path

    from tests.debugging.exploration._config import config

    assert config.output_file == Path("expl.txt")
