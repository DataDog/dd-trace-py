import os

import pytest

from ddtrace.commands import ddtrace_experiment as cli
import ddtrace.llmobs as llmobs_pkg
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


class _FakeDataset:
    def __init__(self, records):
        self._records = [dict(r) for r in records]
        self.pushed = False
        self.url = "http://ds"
        self._id = "ds1"

    def __iter__(self):
        return iter(self._records)

    def __getitem__(self, i):
        return self._records[i]

    def update(self, i, rec):
        self._records[i].update(rec)

    def push(self):
        self.pushed = True


def test_publish_baseline_runs_real_subject_and_backfills_expected(monkeypatch):
    state: dict = {}

    class _Exp:
        def __init__(self, name, task, evaluators):
            self.name, self._task, self.evaluators, self._id = name, task, evaluators, "id-" + name

        def run(self):
            # emulate the engine running the task over each dataset input (records cases in CAPTURE)
            for r in state["dataset"]._records:
                self._task(r["input_data"], None)

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def create_dataset(name, project_name=None, description="", records=None):
            state["dataset_name"] = name
            state["dataset"] = _FakeDataset(records or [])
            return state["dataset"]

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            e = _Exp(name, task, evaluators)
            state.setdefault("exps", []).append(e)
            return e

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="portfolio", inputs=["tickers"], output=lambda ret: ret[0])
    def analyze(tickers):
        return ({"tickers": tickers, "n": len(tickers)}, {"span_id": "s", "trace_id": "t"})

    result = sdk.publish_baseline("portfolio", [{"tickers": ["NVDA"]}, {"tickers": ["AAPL", "MSFT"]}])

    # dataset created from inputs with a UNIQUE name (never accumulates across captures)
    assert state["dataset_name"].startswith("inline-experiment-portfolio-")
    assert result["dataset_name"] == state["dataset_name"]  # returned for the sidecar
    assert [e.name for e in state["exps"]] == ["inline-portfolio-baseline"]
    assert all(getattr(e, "__name__", "") != "regression_match" for e in (state["exps"][0].evaluators or []))
    # expected_output backfilled from the real run's captured outputs, then pushed
    recs = state["dataset"]._records
    assert recs[0]["expected_output"] == {"tickers": ["NVDA"], "n": 1}
    assert recs[1]["expected_output"] == {"tickers": ["AAPL", "MSFT"], "n": 2}
    assert state["dataset"].pushed is True
    assert result["baseline"]._id == "id-inline-portfolio-baseline"


def test_publish_current_stacks_guard_and_reuses_shared_dataset(monkeypatch):
    captured: dict = {}

    class _Exp:
        _id = "cur"

        def run(self):
            pass

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            captured["pulled"] = name  # reuse the dataset published at capture
            return object()

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            captured["name"], captured["evaluators"] = name, evaluators
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    def my_check(input_data, output_data, expected_output):
        return True

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [my_check])
    def f(x):
        return x

    sdk.publish_current(
        "e", [{"input": {"x": 1}, "output": 1}], runner.structural, dataset_name="inline-experiment-e-abc123"
    )
    assert captured["pulled"] == "inline-experiment-e-abc123"  # pulls the exact capture dataset
    assert captured["name"] == "inline-e"
    evs = captured["evaluators"]
    assert evs[0].__name__ == "regression_match" and evs[1] is my_check  # guard first, user stacked


def test_publish_current_falls_back_to_creating_dataset(monkeypatch):
    captured: dict = {}

    class _Exp:
        _id = "cur"

        def run(self):
            pass

    class _FakeLLMObs:
        enabled = True

        @staticmethod
        def pull_dataset(name, project_name=None):
            raise RuntimeError("no baseline dataset")

        @staticmethod
        def create_dataset(name, project_name=None, description="", records=None):
            captured["records"] = records
            return object()

        @staticmethod
        def experiment(name, task, dataset, evaluators=None, **kw):
            return _Exp()

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return x

    sdk.publish_current("e", [{"input": {"x": 1}, "output": 9}], runner.structural)
    assert captured["records"] == [{"input_data": {"x": 1}, "expected_output": 9}]


def test_compare_url_from_ids():
    u = sdk.compare_url_from_ids("BASE", "CUR", project_name="proj x")
    assert u.endswith("/llm/experiments/BASE?compareTargetExperimentId=CUR&project=proj%20x")
    assert sdk.compare_url_from_ids(None, "CUR") is None  # need both ids


def test_publish_state_sidecar(tmp_path):
    base = str(tmp_path / "b.json")
    runner.save_publish_state(base, "portfolio", baseline_experiment_id="X")
    runner.save_publish_state(base, "portfolio", dataset_id="D")  # merges into the same entry
    assert runner.load_publish_state(base, "portfolio") == {"baseline_experiment_id": "X", "dataset_id": "D"}
    assert runner.load_publish_state(base, "missing") is None


# --- CLI helpers ----------------------------------------------------------- #
def test_cli_arg_parser():
    p = cli._build_arg_parser()
    a = p.parse_args(["capture", "myapp:gen", "--out", "b.json"])
    assert a.command == "capture" and a.target == "myapp:gen" and a.out == "b.json"
    a = p.parse_args(["capture", "myapp", "--publish", "--project", "p", "--experiment-name", "e"])
    assert a.publish is True and a.project == "p" and a.experiment_name == "e"
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
