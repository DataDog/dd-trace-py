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


# --- SDK bridge ------------------------------------------------------------ #
def test_sdk_bridge_task_replays_subject():
    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return {"v": x, "ts": "volatile"}

    _set_mode(Mode.REPLAY)
    task = sdk._make_task("e")
    assert task({"x": 5}, None) == {"v": 5, "ts": "volatile"}  # task re-invokes the subject


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


# --- CLI helpers ----------------------------------------------------------- #
def test_cli_arg_parser():
    p = cli._build_arg_parser()
    # `run` and `list` are the only verbs; capture/replay were removed.
    assert p.parse_args(["run", "myapp"]).command == "run"
    assert p.parse_args(["list", "myapp"]).command == "list"
    with pytest.raises(SystemExit):
        p.parse_args(["capture", "myapp:gen"])
    with pytest.raises(SystemExit):
        p.parse_args(["replay", "myapp"])
    # --env-file is shared across subcommands (default None -> falls back to .env)
    assert p.parse_args(["run", "m", "--env-file", "x.env"]).env_file == "x.env"
    assert p.parse_args(["run", "myapp"]).env_file is None
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
    url = "http://ds/inline-experiment-portfolio"

    def __init__(self, records):
        self._records = [dict(r) for r in (records or [])]
        self.pushed = 0

    def __len__(self):
        return len(self._records)

    def __getitem__(self, i):
        return self._records[i]

    def update(self, i, rec):
        self._records[i].update(rec)

    def append(self, rec):
        self._records.append(dict(rec))

    def delete(self, i):
        del self._records[i]

    def push(self):
        self.pushed += 1

    def inputs(self):
        return [r["input_data"] for r in self._records]


def test_sync_dataset_diffs_to_current_inputs():
    ds = _FakeDataset([{"input_data": {"t": "a"}}, {"input_data": {"t": "b"}}, {"input_data": {"t": "c"}}])
    counts = sdk.sync_dataset(ds, [{"t": "c"}, {"t": "d"}, {"t": "e"}])
    # end state = EXACTLY the current inputs; c kept (its record survives), a/b deleted, d/e added
    assert ds.inputs() == [{"t": "c"}, {"t": "d"}, {"t": "e"}]
    assert counts == {"added": 2, "deleted": 2, "kept": 1, "pushed": True}
    assert ds.pushed == 1


def test_sync_dataset_noop_when_unchanged():
    ds = _FakeDataset([{"input_data": {"t": "a"}}, {"input_data": {"t": "b"}}])
    counts = sdk.sync_dataset(ds, [{"t": "a"}, {"t": "b"}])
    assert counts == {"added": 0, "deleted": 0, "kept": 2, "pushed": True}
    assert ds.pushed == 0  # no diff -> no push -> new version not created


def test_sync_dataset_surfaces_push_failure(monkeypatch):
    ds = _FakeDataset([{"input_data": {"t": "a"}}])

    def _boom():
        raise RuntimeError("backend down")

    monkeypatch.setattr(ds, "push", _boom)
    counts = sdk.sync_dataset(ds, [{"t": "b"}])  # add b + delete a -> push attempted, fails
    # the failure is surfaced (pushed=False + a warning), not swallowed as success
    assert counts["added"] == 1 and counts["deleted"] == 1 and counts["pushed"] is False


def test_publish_run_syncs_stable_dataset_and_uses_subject_evaluators(monkeypatch):
    seen = {}
    ds = _FakeDataset([])

    class _Exp:
        _id = "run-1"
        url = "http://exp/run-1"

        def run(self):
            return _FakeRun([{"input": {"x": 1}, "output": 11}])

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            seen["pulled"] = name
            return ds

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            seen["ename"] = name
            seen["evaluators"] = evaluators
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    def my_check(input_data, output_data, expected_output):
        return True

    @experiment_start(name="portfolio", inputs=["x"], output=lambda r: r, evaluators=lambda: [my_check])
    def f(x):
        return x

    out = sdk.publish_run("portfolio", [{"x": 1}])
    assert seen["pulled"] == "inline-experiment-portfolio"  # one stable dataset per subject
    assert seen["ename"].startswith("portfolio-")  # timestamped -> distinct run in the UI
    assert seen["evaluators"] == [my_check]  # subject's OWN evaluators only; no regression guard
    assert ds.inputs() == [{"x": 1}]  # dataset synced to this run's inputs
    assert out["experiment_id"] == "run-1"
    assert out["dataset_name"] == "inline-experiment-portfolio"
    assert out["dataset_url"] == "http://ds/inline-experiment-portfolio"  # surfaced for the CLI
    assert out["sync"] == {"added": 1, "deleted": 0, "kept": 0, "pushed": True}
    assert out["pairs"] == [({"x": 1}, 11)]


def test_publish_run_falls_back_to_marker_without_subject_evaluators(monkeypatch):
    seen = {}

    class _Exp:
        _id = "r"
        url = "u"

        def run(self):
            return _FakeRun([])

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            return _FakeDataset([])

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            seen["evaluators"] = evaluators
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    sdk.publish_run("e", [{"x": 1}])
    assert [getattr(ev, "__name__", None) for ev in seen["evaluators"]] == ["output_present"]


def test_publish_run_creates_dataset_when_absent(monkeypatch):
    seen = {}

    class _Exp:
        _id = "r"
        url = "u"

        def run(self):
            return _FakeRun([])

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            raise ValueError("dataset not found")

        @staticmethod
        def create_dataset(name, project_name=None, description="", records=None):
            seen["created"] = name
            return _FakeDataset(records)

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    sdk.publish_run("e", [{"x": 1}])
    assert seen["created"] == "inline-experiment-e"  # stable name, created empty then synced


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


def test_cli_run_publish_first_publish_freezes_baseline(tmp_path, monkeypatch):
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

    def fake_run(name, inputs, project_name=None, experiment_name=None):
        calls["run"] = (name, inputs, project_name)
        return {
            "experiment_id": "base-1",
            "url": "http://b",
            "dataset_name": "inline-experiment-e",
            "sync": {"added": 1, "deleted": 0, "kept": 0},
            "pairs": [({"x": 1}, 11)],
        }

    monkeypatch.setattr(sdk, "publish_run", fake_run)

    args = cli._build_arg_parser().parse_args(["run", "mymod", "--publish", "--project", "proj", "--baseline-file", p])
    cli._cmd_run(args, ie, runner)
    assert calls["run"] == ("e", [{"x": 1}], "proj")
    # first publish freezes the baseline id + dataset name and seeds the local baseline
    assert runner.load_publish_meta(p, "e") == {
        "dataset_name": "inline-experiment-e",
        "baseline_experiment_id": "base-1",
        "project": "proj",
    }
    assert runner.load_baselines(p)["e"] == [{"input": {"x": 1}, "output": 11}]


def test_cli_run_publish_rerun_compares_to_frozen_baseline(tmp_path, monkeypatch):
    p = str(tmp_path / "b.json")
    runner.save_publish_meta(p, "e", dataset_name="inline-experiment-e", baseline_experiment_id="base-1")
    runner.write_baseline_cases(p, "e", [({"x": 1}, 11)])  # must stay frozen across the rerun
    calls = {}
    monkeypatch.setattr(cli, "_enable_llmobs", lambda ml: "app")
    monkeypatch.setattr(cli, "_flush_llmobs", lambda: None)
    monkeypatch.setattr(sdk, "experiment_exists", lambda eid: True)  # frozen baseline still live
    monkeypatch.setattr(sdk, "compare_url_from_ids", lambda b, c, proj=None: "cmp:%s->%s" % (b, c))

    class _Mod:
        SUBJECT = "e"
        INPUTS = [{"x": 2}]  # the user changed the input this iteration

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    monkeypatch.setattr(cli, "_import_target", lambda t: (_Mod, None))

    def fake_run(name, inputs, project_name=None, experiment_name=None):
        calls["run"] = (name, inputs, project_name)
        return {
            "experiment_id": "cur-2",
            "url": "http://c",
            "dataset_name": "inline-experiment-e",
            "sync": {"added": 1, "deleted": 1, "kept": 0},
            "pairs": [({"x": 2}, 22)],
        }

    monkeypatch.setattr(sdk, "publish_run", fake_run)

    args = cli._build_arg_parser().parse_args(["run", "mymod", "--publish", "--baseline-file", p])
    cli._cmd_run(args, ie, runner)
    assert calls["run"] == ("e", [{"x": 2}], None)  # runs the CURRENT inputs (refresh)
    # the frozen baseline id + local baseline are unchanged by the rerun
    assert runner.load_publish_meta(p, "e")["baseline_experiment_id"] == "base-1"
    assert runner.load_baselines(p)["e"] == [{"input": {"x": 1}, "output": 11}]


def test_rows_pairs_reads_dict_result_and_skips_only_real_errors():
    # experiment.run() returns an ExperimentResult *dict* whose rows always carry an "error"
    # dict (present even on success, with a null message). _rows_pairs must read "rows" from
    # the dict and treat a row as failed only when error["message"] is set — otherwise it would
    # drop every successful row and write an empty local baseline after the frozen publish.
    result = {
        "rows": [
            {"input": {"q": 1}, "output": "a", "error": {"message": None, "stack": None, "type": None}},
            {"input": {"q": 2}, "output": "b", "error": None},
            {"input": {"q": 3}, "output": "c", "error": {"message": "boom", "type": "X", "stack": "..."}},
        ]
    }
    assert sdk._rows_pairs(result) == [({"q": 1}, "a"), ({"q": 2}, "b")]


def test_publish_inputs_explicit_empty_module_inputs_beats_stale_baseline(tmp_path):
    # A module that explicitly cleared its publish cases (INPUTS = []) must win over any prior
    # baseline, so the CLI reports "no inputs" instead of refreshing a paid experiment over
    # stale cases. Absent INPUTS still falls back to the baseline.
    p = str(tmp_path / "b.json")
    runner.write_baseline_cases(p, "e", [({"x": 1}, 11)])  # a prior baseline exists on disk

    class _ModEmpty:
        INPUTS: list = []

    class _ModAbsent:
        pass

    assert cli._publish_inputs("e", _ModEmpty, p, runner) == []  # explicit [] wins -> no inputs
    assert cli._publish_inputs("e", _ModAbsent, p, runner) == [{"x": 1}]  # absent -> baseline


def test_evaluate_one_reports_publish_only_evaluators(monkeypatch):
    # deepeval / pydantic-evals adapters aren't scorable locally; the engine wraps them on
    # --publish. evaluate_one should report them as publish-only rather than crashing.
    from ddtrace.llmobs import _experiment as _exp

    monkeypatch.setattr(_exp, "_is_function_evaluator", lambda e: False)
    (r,) = runner.evaluate_one(object(), recorded=1, new=1, input_data={})
    assert r["error"] == "unsupported locally (publish-only)" and r["assessment"] is None


def test_sdk_task_replays_async_subject():
    # The engine invokes the (sync) task via asyncio.to_thread — a worker thread with no
    # running loop — so _invoke's asyncio.run(...) for an ASYNC subject is safe on --publish.
    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    async def f(x):
        import asyncio as _a

        await _a.sleep(0)
        return {"v": x}

    _set_mode(Mode.REPLAY)
    task = sdk._make_task("e")
    assert task({"x": 7}, None) == {"v": 7}


def test_publish_run_aborts_before_experiment_when_push_fails(monkeypatch):
    ran = {"experiment": False}

    class _DS(_FakeDataset):
        def push(self):
            raise RuntimeError("backend down")

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            return _DS([{"input_data": {"x": 9}}])  # has a stale record -> sync will push

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            ran["experiment"] = True
            raise AssertionError("experiment must not run when the dataset push failed")

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    with pytest.raises(sdk.PublishAbort):
        sdk.publish_run("e", [{"x": 1}])  # add x=1 + delete x=9 -> push -> fails -> abort
    assert ran["experiment"] is False  # aborted BEFORE the paid run, so no bad baseline recorded


def test_publish_run_restores_mode_after_run_exception(monkeypatch):
    from ddtrace.llmobs._inline_experiment import _get_mode

    class _Exp:
        _id = "r"
        url = "u"

        def run(self):
            raise RuntimeError("boom")

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            return _FakeDataset([])

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    with pytest.raises(RuntimeError):
        sdk.publish_run("e", [{"x": 1}])
    assert _get_mode() is Mode.OFF  # finally: restored -> no global-state leak


def test_experiment_exists(monkeypatch):
    class _Ok:
        @staticmethod
        def pull_experiment(eid):
            return object()

    class _Gone:
        @staticmethod
        def pull_experiment(eid):
            raise RuntimeError("404")

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _Ok)
    assert sdk.experiment_exists("exp-1") is True
    assert sdk.experiment_exists(None) is False  # nothing to check
    monkeypatch.setattr(llmobs_pkg, "LLMObs", _Gone)
    assert sdk.experiment_exists("exp-1") is False  # dead pointer


def test_cli_run_publish_aborts_on_push_failure(tmp_path, monkeypatch):
    p = str(tmp_path / "b.json")
    monkeypatch.setattr(cli, "_enable_llmobs", lambda ml: "app")
    monkeypatch.setattr(cli, "_flush_llmobs", lambda: None)

    class _Mod:
        SUBJECT = "e"
        INPUTS = [{"x": 1}]

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    monkeypatch.setattr(cli, "_import_target", lambda t: (_Mod, None))

    def _abort(*a, **k):
        raise sdk.PublishAbort("could not persist dataset")

    monkeypatch.setattr(sdk, "publish_run", _abort)

    args = cli._build_arg_parser().parse_args(["run", "mymod", "--publish", "--baseline-file", p])
    with pytest.raises(SystemExit) as e:
        cli._cmd_run(args, ie, runner)
    assert e.value.code == 1
    assert not runner.load_publish_meta(p, "e")  # no baseline frozen on abort


def test_cli_run_publish_dead_baseline_pointer_suggests_rerecord(tmp_path, monkeypatch, capsys):
    p = str(tmp_path / "b.json")
    runner.save_publish_meta(p, "e", dataset_name="inline-experiment-e", baseline_experiment_id="gone-1")
    runner.write_baseline_cases(p, "e", [({"x": 1}, 11)])
    monkeypatch.setattr(cli, "_enable_llmobs", lambda ml: "app")
    monkeypatch.setattr(cli, "_flush_llmobs", lambda: None)
    monkeypatch.setattr(sdk, "experiment_exists", lambda eid: False)  # baseline deleted in UI
    compared = {"n": 0}
    monkeypatch.setattr(sdk, "compare_url_from_ids", lambda *a, **k: compared.__setitem__("n", 1) or "cmp")

    class _Mod:
        SUBJECT = "e"
        INPUTS = [{"x": 2}]

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    monkeypatch.setattr(cli, "_import_target", lambda t: (_Mod, None))
    monkeypatch.setattr(
        sdk,
        "publish_run",
        lambda *a, **k: {
            "experiment_id": "cur",
            "url": "u",
            "dataset_name": "inline-experiment-e",
            "dataset_url": "du",
            "sync": {"added": 0, "deleted": 0, "kept": 1, "pushed": True},
            "pairs": [({"x": 2}, 22)],
        },
    )

    args = cli._build_arg_parser().parse_args(["run", "mymod", "--publish", "--baseline-file", p])
    cli._cmd_run(args, ie, runner)
    out = capsys.readouterr().out
    assert "no longer exists" in out and "--record" in out  # suggests re-recording
    assert compared["n"] == 0  # dead pointer -> no compare link printed
