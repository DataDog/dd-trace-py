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

    cu = sdk.compare_url(result["baseline"], result["current"])  # compare view links both runs
    assert cu and cu.endswith("experiments=id-inline-e-baseline,id-inline-e")


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
