import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def results_checker():
    path = Path(__file__).resolve().parents[2] / ".gitlab/benchmarks/steps/check-results.py"
    spec = importlib.util.spec_from_file_location("check_benchmark_results", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(configuration, values):
    return {
        "version": "1.0",
        "metadata": {"unit": "second"},
        "benchmarks": [{"metadata": {"name": f"eventsapiscenario-{configuration}"}, "runs": [{"values": values}]}],
    }


def test_benchmark_results_require_every_config_on_both_sides(results_checker, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("subscriber_setup: {}\nsubscriber_registration: {}\n")
    directories = [tmp_path / "baseline", tmp_path / "candidate"]
    for directory in directories:
        directory.mkdir()
        for name in ("subscriber_setup", "subscriber_registration"):
            (directory / f"results.{name}.json").write_text(json.dumps(_result(name, [0.001, 0.002])))

    results_checker.validate_results(config, directories)
    missing = directories[0] / "results.subscriber_registration.json"
    missing.unlink()
    with pytest.raises(ValueError, match="baseline/results.subscriber_registration.json"):
        results_checker.validate_results(config, directories)


@pytest.mark.parametrize(
    "result",
    [
        {"benchmarks": []},
        _result("subscriber_setup", []),
        _result("subscriber_setup", [0]),
        _result("subscriber_setup", [float("nan")]),
        _result("subscriber_setup", [True]),
        _result("other_config", [0.001]),
        {
            "benchmarks": [
                {"metadata": {"name": "eventsapiscenario-subscriber_setup"}, "runs": [{"warmups": [[1, 0.001]]}]}
            ]
        },
    ],
)
def test_benchmark_results_reject_missing_or_invalid_measurements(results_checker, tmp_path, result):
    config = tmp_path / "config.yaml"
    config.write_text("subscriber_setup: {}\n")
    (tmp_path / "results.subscriber_setup.json").write_text(json.dumps(result))
    with pytest.raises(ValueError, match="results.subscriber_setup.json"):
        results_checker.validate_results(config, [tmp_path])
