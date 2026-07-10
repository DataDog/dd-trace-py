import os

import pytest

from ddtrace.commands import ddtrace_experiment as cli
import ddtrace.llmobs as llmobs_pkg
from ddtrace.llmobs import _inline_experiment as ie
from ddtrace.llmobs import _inline_experiment_runner as runner
from ddtrace.llmobs import _inline_experiment_sdk as sdk
from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import MultiEvaluatorResult
from ddtrace.llmobs._inline_experiment import Mode
from ddtrace.llmobs._inline_experiment import _reset
from ddtrace.llmobs._inline_experiment import _set_mode
from ddtrace.llmobs._inline_experiment import experiment_end
from ddtrace.llmobs._inline_experiment import experiment_start


@pytest.fixture(autouse=True)
def reset_inline_experiments():
    _reset()
    yield
    _reset()


# --- comparators ----------------------------------------------------------- #
def test_comparators():
    base = {"generated_at": "t1", "analyses": [{"t": "NVDA"}], "summary": "a"}
    text = {"generated_at": "t2", "analyses": [{"t": "NVDA"}], "summary": "b"}  # text/ts drift only
    drop = {"generated_at": "t2", "analyses": [], "summary": "b"}  # structural change

    assert runner.exact(base, base) and not runner.exact(base, text)
    assert runner.structural(base, text) and not runner.structural(base, drop)
    assert runner.ignoring("generated_at", "summary")(base, text)
    assert not runner.ignoring("generated_at")(base, text)  # summary still differs


def test_comparator_from_spec():
    assert runner.comparator_from_spec("exact") is runner.exact
    assert runner.comparator_from_spec("structural") is runner.structural
    assert runner.comparator_from_spec("structural", ["k"])({"k": 1, "a": 1}, {"k": 2, "a": 1})  # ignoring k
    with pytest.raises(ValueError):
        runner.comparator_from_spec("bogus")


# --- comparisons as evaluators --------------------------------------------- #
def test_as_evaluator_maps_recorded_to_expected_and_reports_match_changed():
    ev = runner.as_evaluator(runner.exact)
    assert ev.__name__ == "regression_match"  # default comparison label
    # evaluator shape (input_data, output_data, expected_output); recorded baseline == expected
    matched = ev({}, "x", "x")
    assert matched.value is True and matched.assessment == "match"
    changed = ev({}, "y", "x")
    assert changed.value is False and changed.assessment == "changed"


def test_comparison_factory_builds_named_match_changed_evaluators():
    ex = runner.comparison("exact")
    assert ex.__name__ == "exact_match"
    assert ex({}, {"v": 1}, {"v": 1}).assessment == "match"
    assert ex({}, {"v": 1}, {"v": 2}).assessment == "changed"

    ig = runner.comparison("ignoring", ignore=["ts"])
    assert ig.__name__ == "ignoring_match"
    assert ig({}, {"v": 1, "ts": "new"}, {"v": 1, "ts": "old"}).assessment == "match"  # ts ignored

    st = runner.comparison()  # default kind is structural
    assert st.__name__ == "structural_match"
    assert st({}, {"v": 2, "shape": [9]}, {"v": 1, "shape": [1]}).assessment == "match"  # same shape


def test_comparison_used_as_attached_evaluator():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [runner.comparison("exact")])
    def f(x):
        return {"v": x}

    f(1)
    _set_mode(Mode.REPLAY)
    # evals = [default regression_match (structural), attached exact_match]; both match here
    rows = runner.replay("e", runner.structural, score_evaluators=True)
    verdicts = {e["name"]: e["assessment"] for e in rows[0]["evals"]}
    assert verdicts == {"regression_match": "match", "exact_match": "match"}


# --- replay ---------------------------------------------------------------- #
def test_replay_single_function_match_text_drift_and_structural_change():
    state = {"drift": ""}
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["tickers"], output=lambda r: r)
    def analyze(tickers):
        analyses = [{"ticker": t} for t in tickers]
        if state["drift"] == "drop":
            analyses = analyses[:-1]
        return {"analyses": analyses, "summary": "y" if state["drift"] == "text" else "x", "generated_at": "t"}

    analyze(["NVDA", "AMD"])  # baseline
    _set_mode(Mode.REPLAY)

    # the default comparison evaluator reports match/changed (no privileged status)
    assert runner.replay("e", runner.structural)[0]["evals"][0]["assessment"] == "match"
    state["drift"] = "text"
    assert runner.replay("e", runner.structural)[0]["evals"][0]["assessment"] == "match"  # tolerated
    state["drift"] = "drop"
    assert runner.replay("e", runner.structural)[0]["evals"][0]["assessment"] == "changed"  # caught


def test_replay_emit_shape():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e")
    def ingest(q):
        return emit(q.upper())

    @experiment_end(name="e")
    def emit(result):
        return result

    ingest("hi")
    _set_mode(Mode.REPLAY)
    rows = runner.replay("e", runner.exact)
    assert rows[0]["exec"] == "OK" and rows[0]["new"] == "HI" and rows[0]["evals"][0]["assessment"] == "match"


def test_replay_uses_fixtures_to_rebuild_infra():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["msg"], output=lambda r: r, fixtures=lambda: {"infra": object()})
    def run(infra, msg):
        assert infra is not None  # infra supplied by fixtures at replay
        return msg.upper()

    run(object(), "hi")
    _set_mode(Mode.REPLAY)
    assert runner.replay("e", runner.exact)[0]["evals"][0]["assessment"] == "match"


def test_replay_surfaces_task_error_as_row():
    state = {"boom": False}
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        if state["boom"]:
            raise ValueError("boom")
        return x

    f(1)
    _set_mode(Mode.REPLAY)
    state["boom"] = True
    rows = runner.replay("e", runner.exact)
    # execution failed -> exec=ERROR and nothing to evaluate
    assert rows[0]["exec"] == "ERROR" and "boom" in rows[0]["new"] and rows[0]["evals"] == []


# --- persistence ----------------------------------------------------------- #
def test_save_and_load_baselines(tmp_path):
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return {"v": x}

    f(1)
    path = str(tmp_path / "b.json")
    assert runner.save_baselines(path) == {"e": [{"input": {"x": 1}, "output": {"v": 1}}]}
    assert runner.load_baselines(path) == {"e": [{"input": {"x": 1}, "output": {"v": 1}}]}


def test_replay_from_loaded_baseline_cases():
    # baseline produced by a previous (separate) capture, replayed against current code
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return {"v": x, "shape": [1, 2]}

    _set_mode(Mode.REPLAY)
    cases = [{"input": {"x": 7}, "output": {"v": 7, "shape": [9, 9]}}]  # different leaf values, same shape
    assert runner.replay("e", runner.structural, cases=cases)[0]["evals"][0]["assessment"] == "match"
    cases = [{"input": {"x": 7}, "output": {"v": 7, "shape": [9]}}]  # different shape
    assert runner.replay("e", runner.structural, cases=cases)[0]["evals"][0]["assessment"] == "changed"


# --- SDK bridge (Slice C) -------------------------------------------------- #
def test_sdk_bridge_task_replays_subject_and_evaluator_wraps_comparator():
    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return {"v": x, "ts": "volatile"}

    _set_mode(Mode.REPLAY)
    task = sdk._make_task("e")
    assert task({"x": 5}, None) == {"v": 5, "ts": "volatile"}  # task re-invokes the subject

    ev = sdk._make_evaluator(runner.structural)
    match = ev({}, {"v": 5, "ts": "a"}, {"v": 9, "ts": "b"})  # same shape
    assert match.value is True and match.assessment == "match"
    changed = ev({}, {"v": 5}, {"v": 9, "extra": 1})  # extra field
    assert changed.value is False and changed.assessment == "changed"


# --- evaluators ------------------------------------------------------------ #
def test_resolve_evaluators():
    def fn(input_data, output_data, expected_output):
        return True

    assert runner.resolve_evaluators(None) == []
    assert runner.resolve_evaluators([fn]) == [fn]  # list used as-is
    assert runner.resolve_evaluators(lambda: [fn]) == [fn]  # thunk is called
    assert runner.resolve_evaluators(fn) == [fn]  # bare single -> wrapped


def test_evaluate_one_dispatches_function_class_and_errors():
    def eq(input_data, output_data, expected_output):  # plain function returning bool
        return output_data == expected_output

    (r,) = runner.evaluate_one(eq, recorded=1, new=1, input_data={})  # one evaluator -> one row
    assert r["name"] == "eq" and r["assessment"] == "pass" and r["value"] is True

    class MyJudge(BaseEvaluator):  # class evaluator returning a full EvaluatorResult
        def __init__(self):
            super().__init__(name="myjudge")

        def evaluate(self, ctx):
            ok = ctx.output_data == ctx.expected_output
            return EvaluatorResult(value=ok, assessment="pass" if ok else "fail", reasoning="r")

    (r,) = runner.evaluate_one(MyJudge(), recorded="a", new="b", input_data={})
    assert r["name"] == "myjudge" and r["assessment"] == "fail" and r["reasoning"] == "r"

    def boom(input_data, output_data, expected_output):  # an evaluator error becomes a row, never propagates
        raise ValueError("x")

    (r,) = runner.evaluate_one(boom, recorded=1, new=1, input_data={})
    assert r["error"] and r["assessment"] is None


def test_evaluate_one_expands_multi_evaluator_result():
    class Multi(BaseEvaluator):  # one evaluator emitting several named sub-metrics
        def __init__(self):
            super().__init__(name="quality")

        def evaluate(self, ctx):
            return MultiEvaluatorResult(
                {
                    "precision": EvaluatorResult(value=True, assessment="pass"),
                    "recall": EvaluatorResult(value=False, assessment="fail"),
                }
            )

    rows = runner.evaluate_one(Multi(), recorded="a", new="b", input_data={})
    # expands to one row per sub-metric, prefixed by the evaluator name (mirrors --publish)
    assert {r["name"]: r["assessment"] for r in rows} == {"quality-precision": "pass", "quality-recall": "fail"}


def test_replay_scores_attached_evaluators_only_when_enabled():
    _set_mode(Mode.CAPTURE)

    def at_least_as_long(input_data, output_data, expected_output):
        return len(output_data) >= len(expected_output)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [at_least_as_long])
    def f(x):
        return x

    f("hello")
    _set_mode(Mode.REPLAY)

    # the default comparison evaluator always runs; the attached one only with score_evaluators
    off = runner.replay("e", runner.exact)[0]["evals"]
    assert [e["name"] for e in off] == ["regression_match"]
    on = {e["name"]: e["assessment"] for e in runner.replay("e", runner.exact, score_evaluators=True)[0]["evals"]}
    assert on["regression_match"] == "match" and on["at_least_as_long"] == "pass"


def test_publish_stacks_user_evaluators_behind_guard(monkeypatch):
    captured: dict = {}

    class _Exp:
        url = "http://exp"

        def run(self):
            pass

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def create_dataset(name, **kw):
            return object()

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            captured["evaluators"] = evaluators
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    def my_check(input_data, output_data, expected_output):
        return output_data == expected_output

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [my_check])
    def f(x):
        return x

    sdk.run_as_experiment("e", [{"input": {"x": 1}, "output": 1}], runner.structural)
    evs = captured["evaluators"]
    assert evs[0].__name__ == "regression_match"  # always-on guard first
    assert evs[1] is my_check  # user evaluator stacked on top


def test_publish_creates_baseline_and_current_over_same_dataset(monkeypatch):
    created = []
    datasets = []

    class _Exp:
        def __init__(self, name):
            self.name = name
            self._id = "id-" + name

        def run(self):
            pass

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def create_dataset(name, **kw):
            datasets.append(name)
            return object()

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            created.append((name, task))
            return _Exp(name)

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    result = sdk.run_as_experiment("e", [{"input": {"x": 1}, "output": 11}], runner.structural)

    names = [n for n, _ in created]
    assert names == ["inline-e-baseline", "inline-e"]  # baseline reference first, then current
    assert datasets == ["inline-experiment-e"]  # both runs share one dataset

    baseline_task = created[0][1]  # the baseline task echoes the captured output (no re-run)
    assert baseline_task({"x": 1}, None) == 11

    # compare view = baseline experiment page (first id) with current as the compare target
    cu = sdk.compare_url(result["baseline"], result["current"], project_name="proj x")
    assert cu and "/llm/experiments/id-inline-e-baseline?compareTargetExperimentId=id-inline-e" in cu
    assert cu.endswith("&project=proj%20x")  # project is url-encoded


def test_publish_baseline_can_be_disabled(monkeypatch):
    created = []

    class _E:
        _id = "x"

        def run(self):
            pass

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def create_dataset(name, **kw):
            return object()

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            created.append(name)
            return _E()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    result = sdk.run_as_experiment("e", [{"input": {"x": 1}, "output": 1}], runner.structural, publish_baseline=False)
    assert created == ["inline-e"]  # only the current run, no baseline
    assert result["baseline"] is None


# --- CLI helpers ----------------------------------------------------------- #
def test_cli_arg_parser():
    p = cli._build_arg_parser()
    a = p.parse_args(["capture", "myapp:gen", "--out", "b.json"])
    assert a.command == "capture" and a.target == "myapp:gen" and a.out == "b.json"
    a = p.parse_args(["replay", "myapp", "--ignore", "a,b"])
    assert a.command == "replay" and a.ignore == "a,b" and a.comparator == "structural"
    a = p.parse_args(["replay", "myapp", "--publish", "--project", "p", "--ml-app", "m"])
    assert a.publish is True and a.project == "p" and a.ml_app == "m"
    assert p.parse_args(["replay", "myapp", "--evaluate"]).evaluate is True
    assert p.parse_args(["replay", "myapp"]).evaluate is False
    assert p.parse_args(["list", "myapp"]).command == "list"
    # --env-file is shared across all subcommands (default None -> falls back to .env)
    assert p.parse_args(["capture", "m:e", "--env-file", "x.env"]).env_file == "x.env"
    assert p.parse_args(["replay", "myapp"]).env_file is None
    assert p.parse_args(["list", "myapp", "--env-file", "y.env"]).env_file == "y.env"


def test_cli_load_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"OPENAI_API_KEY": "from-shell"})
    p = tmp_path / ".env"
    p.write_text(
        "# a comment\n"
        "\n"
        "export DD_LLMOBS_ML_APP=my-app\n"
        'DD_API_KEY="secret123"\n'  # quotes stripped
        "OPENAI_API_KEY=from-file\n"  # must NOT override the exported value
    )
    loaded = cli._load_env_file(str(p))
    assert loaded == 2  # DD_LLMOBS_ML_APP + DD_API_KEY set; OPENAI_API_KEY skipped (already set)
    assert os.environ["DD_LLMOBS_ML_APP"] == "my-app"
    assert os.environ["DD_API_KEY"] == "secret123"
    assert os.environ["OPENAI_API_KEY"] == "from-shell"  # real env wins
    assert cli._load_env_file(str(tmp_path / "missing.env")) == 0  # absent file -> no-op


def _ev(name, assessment, error=None, value=True):
    return {"name": name, "value": value, "assessment": assessment, "reasoning": None, "error": error}


def test_cli_print_report_counts(capsys):
    # `exec` is execution status; the comparison verdict is an evaluator (match/changed)
    rows = [
        {"input": {}, "recorded": 1, "new": 1, "exec": "OK", "evals": [_ev("regression_match", "match")]},
        {"input": {}, "recorded": 1, "new": 2, "exec": "OK", "evals": [_ev("regression_match", "changed")]},
    ]
    counts = cli._print_report("e", rows)
    assert counts["OK"] == 2 and counts["CHANGED"] == 1


def test_cli_print_report_counts_eval_fail(capsys):
    rows = [{"input": {}, "recorded": 1, "new": 1, "exec": "OK", "evals": [_ev("judge", "fail", value=False)]}]
    counts = cli._print_report("e", rows)
    assert counts["OK"] == 1 and counts["EVAL_FAIL"] == 1  # a failing eval is counted for the exit gate


def test_cli_print_report_counts_eval_error(capsys):
    rows = [
        {"input": {}, "recorded": 1, "new": 1, "exec": "OK", "evals": [_ev("judge", None, error="boom", value=None)]}
    ]
    counts = cli._print_report("e", rows)
    # an evaluator that errored (the check didn't run) is counted so it can gate the exit code
    assert counts["OK"] == 1 and counts["EVAL_ERROR"] == 1


# --- unified `run` verb (offline) ------------------------------------------ #
def test_cli_run_arg_parser():
    p = cli._build_arg_parser()
    a = p.parse_args(["run", "myapp:gen"])
    assert a.command == "run" and a.target == "myapp:gen"
    assert a.record is False and a.name is None and a.comparator == "structural" and a.evaluate is False
    a = p.parse_args(
        ["run", "myapp", "--record", "--name", "greet", "--comparator", "exact", "--ignore", "ts", "--evaluate"]
    )
    assert a.record and a.name == "greet" and a.comparator == "exact" and a.ignore == "ts" and a.evaluate
    assert p.parse_args(["run", "m", "--baseline-file", "b.json"]).baseline_file == "b.json"
    assert p.parse_args(["run", "m", "--env-file", "x.env"]).env_file == "x.env"  # shared flag
    a = p.parse_args(["run", "m", "--publish", "--project", "proj", "--experiment-name", "en", "--ml-app", "app"])
    assert a.publish is True and a.project == "proj" and a.experiment_name == "en" and a.ml_app == "app"
    assert p.parse_args(["run", "m"]).publish is False


def test_cli_run_records_then_compares_offline(tmp_path):
    out = str(tmp_path / "b.json")

    @experiment_start(name="greet", inputs=["who"], output=lambda r: r)
    def greet(who):
        return {"msg": "hi " + who}

    def driver():
        greet(who="world")

    # First run: record the baseline (CAPTURE), then restore mode to OFF.
    cli._run_record_offline(driver, out, runner, ie)
    assert ie._get_mode() is Mode.OFF
    data = runner.load_baselines(out)
    assert data["greet"][0]["input"] == {"who": "world"}
    assert data["greet"][0]["output"] == {"msg": "hi world"}

    # Rerun with unchanged code -> MATCH -> exit 0.
    args = cli._build_arg_parser().parse_args(["run", "m", "--baseline-file", out])
    with pytest.raises(SystemExit) as e:
        cli._run_compare_offline(args, ie, runner, out)
    assert e.value.code == 0
    assert ie._get_mode() is Mode.OFF


def test_cli_run_compare_gates_on_change(tmp_path):
    out = str(tmp_path / "b.json")
    state = {"v": 1}

    @experiment_start(name="g", inputs=["x"], output=lambda r: r)
    def g(x):
        return {"v": state["v"], "x": x}

    def driver():
        g(x=1)

    cli._run_record_offline(driver, out, runner, ie)
    state["v"] = 2  # value drift; shape unchanged
    # exact sees the change -> `changed` -> exit 1 (CI gate)
    args = cli._build_arg_parser().parse_args(["run", "m", "--baseline-file", out, "--comparator", "exact"])
    with pytest.raises(SystemExit) as e:
        cli._run_compare_offline(args, ie, runner, out)
    assert e.value.code == 1
    # structural ignores value drift -> match -> exit 0
    args = cli._build_arg_parser().parse_args(["run", "m", "--baseline-file", out, "--comparator", "structural"])
    with pytest.raises(SystemExit) as e:
        cli._run_compare_offline(args, ie, runner, out)
    assert e.value.code == 0


def test_cli_run_record_needs_driver_when_no_baseline():
    with pytest.raises(SystemExit) as e:
        cli._run_record_offline(None, "unused.json", runner, ie)
    assert e.value.code == 2


def test_cli_run_compare_no_matching_subject(tmp_path):
    out = str(tmp_path / "b.json")

    @experiment_start(name="greet", inputs=["who"], output=lambda r: r)
    def greet(who):
        return who

    def driver():
        greet(who="a")

    cli._run_record_offline(driver, out, runner, ie)
    args = cli._build_arg_parser().parse_args(["run", "m", "--baseline-file", out, "--name", "missing"])
    with pytest.raises(SystemExit) as e:
        cli._run_compare_offline(args, ie, runner, out)
    assert e.value.code == 2


# --- `run --publish` (backend, mocked) ------------------------------------- #
class _FakeRun:
    def __init__(self, rows):
        self.rows = rows


class _FakeDataset:
    def __init__(self, records):
        self._records = [dict(r) for r in (records or [])]
        self.pushed = 0

    def __len__(self):
        return len(self._records)

    def __getitem__(self, i):
        return self._records[i]

    def update(self, i, rec):
        self._records[i].update(rec)

    def push(self):
        self.pushed += 1


def test_publish_baseline_creates_input_only_dataset_and_backfills(monkeypatch):
    events = {"created": [], "experiments": [], "dataset": None}

    class _Exp:
        _id = "base-1"
        url = "http://exp/base-1"

        def run(self):
            return _FakeRun([{"input": {"x": 1}, "output": 11}, {"input": {"x": 2}, "output": 22}])

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def create_dataset(name, project_name=None, description="", records=None):
            events["created"].append((name, records))
            events["dataset"] = _FakeDataset(records)
            return events["dataset"]

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            events["experiments"].append((name, evaluators))
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    out = sdk.publish_baseline("e", [{"x": 1}, {"x": 2}], dataset_name="ds-fixed")
    # dataset created with input_data only (no expected yet)
    assert events["created"] == [("ds-fixed", [{"input_data": {"x": 1}}, {"input_data": {"x": 2}}])]
    # baseline experiment named <subject>-baseline; no regression guard (subject has no evaluators)
    ename, evs = events["experiments"][0]
    assert ename == "e-baseline"
    assert [getattr(ev, "__name__", None) for ev in evs] == ["baseline_recorded"]
    # expected_output backfilled from the run's produced outputs, pushed once
    assert events["dataset"]._records[0]["expected_output"] == 11
    assert events["dataset"]._records[1]["expected_output"] == 22
    assert events["dataset"].pushed == 1
    assert out["experiment_id"] == "base-1" and out["dataset_name"] == "ds-fixed"
    assert out["pairs"] == [({"x": 1}, 11), ({"x": 2}, 22)]


def test_publish_baseline_uses_subject_evaluators_not_the_guard(monkeypatch):
    seen = {}

    class _Exp:
        _id = "b"
        url = "u"

        def run(self):
            return _FakeRun([])

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def create_dataset(name, **kw):
            return _FakeDataset([])

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            seen["evaluators"] = evaluators
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    def my_check(input_data, output_data, expected_output):
        return True

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [my_check])
    def f(x):
        return x

    sdk.publish_baseline("e", [{"x": 1}], dataset_name="d")
    assert seen["evaluators"] == [my_check]  # the baseline is scored by the subject's own evaluators only


def test_publish_current_pulls_dataset_stacks_guard_and_builds_compare(monkeypatch):
    seen = {}

    class _Exp:
        _id = "cur-9"
        url = "http://exp/cur-9"

        def run(self):
            pass

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            seen["pulled"] = name
            return object()

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            seen["ename"] = name
            seen["evaluators"] = evaluators
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    def my_check(input_data, output_data, expected_output):
        return True

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [my_check])
    def f(x):
        return x

    out = sdk.publish_current("e", "ds-1", runner.structural, baseline_experiment_id="base-1", project_name="p x")
    assert seen["pulled"] == "ds-1" and seen["ename"] == "e"
    assert seen["evaluators"][0].__name__ == "regression_match"  # guard first
    assert seen["evaluators"][1] is my_check  # then the subject's evaluator
    assert out["experiment_id"] == "cur-9"
    assert "/llm/experiments/base-1?compareTargetExperimentId=cur-9" in out["compare_url"]
    assert out["compare_url"].endswith("&project=p%20x")


def test_compare_url_from_ids():
    assert sdk.compare_url_from_ids(None, "c") is None
    assert sdk.compare_url_from_ids("b", None) is None
    u = sdk.compare_url_from_ids("b1", "c1", project_name="p x")
    assert "/llm/experiments/b1?compareTargetExperimentId=c1" in u
    assert u.endswith("&project=p%20x")


def test_publish_meta_embedded_in_baseline_and_dropped_on_record(tmp_path):
    p = str(tmp_path / "b.json")

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    _set_mode(Mode.CAPTURE)
    f(x=1)
    _set_mode(Mode.OFF)
    runner.save_baselines(p)
    runner.save_publish_meta(p, "e", dataset_name="ds-1", baseline_experiment_id="base-1")
    assert runner.load_publish_meta(p, "e") == {"dataset_name": "ds-1", "baseline_experiment_id": "base-1"}
    # the reserved _publish block is not iterated as a subject
    assert [n for n, _ in runner.subject_items(runner.load_baselines(p))] == ["e"]
    # re-recording rewrites the file from the registry and drops publish state (drift-free)
    runner.save_baselines(p)
    assert runner.load_publish_meta(p, "e") is None


def test_write_baseline_cases_preserves_publish_block(tmp_path):
    p = str(tmp_path / "b.json")
    runner.save_publish_meta(p, "e", dataset_name="ds-1")
    runner.write_baseline_cases(p, "e", [({"x": 1}, 11)])
    assert runner.load_publish_meta(p, "e") == {"dataset_name": "ds-1"}  # meta survives
    assert runner.load_baselines(p)["e"] == [{"input": {"x": 1}, "output": 11}]


def test_cli_run_publish_first_publish_persists_meta(tmp_path, monkeypatch):
    p = str(tmp_path / "b.json")
    calls = {}
    monkeypatch.setattr(cli, "_enable_llmobs", lambda ml: "app")
    monkeypatch.setattr(cli, "_flush_llmobs", lambda: None)

    class _Mod:
        SUBJECT = "e"
        INPUTS = [{"x": 1}]

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    monkeypatch.setattr(cli, "_import_target", lambda t: (_Mod, None))

    def fake_baseline(name, inputs, project_name=None, experiment_name=None, dataset_name=None):
        calls["baseline"] = (name, inputs, project_name)
        return {"experiment_id": "base-1", "url": "http://b", "dataset_name": "ds-1", "pairs": [({"x": 1}, 11)]}

    monkeypatch.setattr(sdk, "publish_baseline", fake_baseline)

    args = cli._build_arg_parser().parse_args(["run", "mymod", "--publish", "--project", "proj", "--baseline-file", p])
    cli._cmd_run(args, ie, runner)
    assert calls["baseline"] == ("e", [{"x": 1}], "proj")
    assert runner.load_publish_meta(p, "e") == {
        "dataset_name": "ds-1",
        "baseline_experiment_id": "base-1",
        "project": "proj",
    }
    assert runner.load_baselines(p)["e"] == [{"input": {"x": 1}, "output": 11}]


def test_cli_run_publish_rerun_uses_saved_meta(tmp_path, monkeypatch):
    p = str(tmp_path / "b.json")
    runner.save_publish_meta(p, "e", dataset_name="ds-1", baseline_experiment_id="base-1")
    calls = {}
    monkeypatch.setattr(cli, "_enable_llmobs", lambda ml: "app")
    monkeypatch.setattr(cli, "_flush_llmobs", lambda: None)

    class _Mod:
        SUBJECT = "e"

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    monkeypatch.setattr(cli, "_import_target", lambda t: (_Mod, None))

    def fake_current(
        name, dataset_name, comparator, baseline_experiment_id=None, project_name=None, experiment_name=None
    ):
        calls["current"] = (name, dataset_name, baseline_experiment_id, project_name)
        return {"experiment_id": "cur-1", "url": "http://c", "compare_url": "http://compare"}

    monkeypatch.setattr(sdk, "publish_current", fake_current)

    args = cli._build_arg_parser().parse_args(["run", "mymod", "--publish", "--project", "proj", "--baseline-file", p])
    cli._cmd_run(args, ie, runner)
    assert calls["current"] == ("e", "ds-1", "base-1", "proj")
