import ast
from pathlib import Path

import pytest

from tests.contrib.patch_scenarios import PATCH_SCENARIOS


@pytest.mark.parametrize("outcome, status", [("pass", 0), ("fail", 1), ("skip", 77), ("xfail", 78)])
def test_patch_scenario_outcomes(python_process, tmp_path, outcome, status):
    fixture = tmp_path / "patch_case.py"
    fixture.write_text(
        "import os, unittest, pytest\n"
        "class Case(unittest.TestCase):\n"
        "    def setUp(self):\n"
        "        assert os.environ['CHILD_SETTING'] == 'configured'\n"
        "    def test_scenario(self):\n"
        f"        outcome = {outcome!r}\n"
        "        if outcome == 'fail': raise AssertionError('scenario failed')\n"
        "        if outcome == 'skip': self.skipTest('scenario unavailable')\n"
        "        if outcome == 'xfail': pytest.xfail('known failure')\n"
    )
    result = python_process(
        f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
        "from tests.contrib.patch import run_scenario; run_scenario('patch_case', 'Case', 'test_scenario')",
        env_overrides={"CHILD_SETTING": "configured"},
    )
    result.check_returncode(status)


def test_patch_scenarios_include_every_base_case():
    source = Path(__file__).parents[1] / "contrib" / "patch.py"
    tree = ast.parse(source.read_text())
    case = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PatchScenario")
    methods = {n.name for n in case.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    assert set(PATCH_SCENARIOS) == methods
    assert len(PATCH_SCENARIOS) == len(methods)
