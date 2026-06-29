import pytest

from ddtrace.commands import ddtrace_experiment as cli
from ddtrace.llmobs import _inline_experiment_runner as runner
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

    assert runner.replay("e", runner.structural)[0]["status"] == "MATCH"
    state["drift"] = "text"
    assert runner.replay("e", runner.structural)[0]["status"] == "MATCH"  # tolerated
    state["drift"] = "drop"
    assert runner.replay("e", runner.structural)[0]["status"] == "CHANGED"  # caught


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
    assert rows[0]["status"] == "MATCH" and rows[0]["new"] == "HI"


def test_replay_uses_fixtures_to_rebuild_infra():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["msg"], output=lambda r: r, fixtures=lambda: {"infra": object()})
    def run(infra, msg):
        assert infra is not None  # infra supplied by fixtures at replay
        return msg.upper()

    run(object(), "hi")
    _set_mode(Mode.REPLAY)
    assert runner.replay("e", runner.exact)[0]["status"] == "MATCH"


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
    assert rows[0]["status"] == "ERROR" and "boom" in rows[0]["new"]


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
    assert runner.replay("e", runner.structural, cases=cases)[0]["status"] == "MATCH"
    cases = [{"input": {"x": 7}, "output": {"v": 7, "shape": [9]}}]  # different shape
    assert runner.replay("e", runner.structural, cases=cases)[0]["status"] == "CHANGED"


# --- SDK bridge (Slice C) -------------------------------------------------- #
def test_sdk_bridge_task_replays_subject_and_evaluator_wraps_comparator():
    from ddtrace.llmobs import _inline_experiment_sdk as sdk

    @experiment_start(name="e", inputs=["x"], output=lambda r: r)
    def f(x):
        return {"v": x, "ts": "volatile"}

    _set_mode(Mode.REPLAY)
    task = sdk._make_task("e")
    assert task({"x": 5}, None) == {"v": 5, "ts": "volatile"}  # task re-invokes the subject

    ev = sdk._make_evaluator(runner.structural)
    assert ev({}, {"v": 5, "ts": "a"}, {"v": 9, "ts": "b"}) is True  # same shape -> match
    assert ev({}, {"v": 5}, {"v": 9, "extra": 1}) is False  # extra field -> no match


# --- evaluators ------------------------------------------------------------ #
def test_resolve_evaluators():
    def fn(input_data, output_data, expected_output):
        return True

    assert runner.resolve_evaluators(None) == []
    assert runner.resolve_evaluators([fn]) == [fn]  # list used as-is
    assert runner.resolve_evaluators(lambda: [fn]) == [fn]  # thunk is called
    assert runner.resolve_evaluators(fn) == [fn]  # bare single -> wrapped


def test_evaluate_one_dispatches_function_class_and_errors():
    from ddtrace.llmobs._experiment import BaseEvaluator
    from ddtrace.llmobs._experiment import EvaluatorResult

    def eq(input_data, output_data, expected_output):  # plain function returning bool
        return output_data == expected_output

    r = runner.evaluate_one(eq, recorded=1, new=1, input_data={})
    assert r["name"] == "eq" and r["assessment"] == "pass" and r["value"] is True

    class MyJudge(BaseEvaluator):  # class evaluator returning a full EvaluatorResult
        def __init__(self):
            super().__init__(name="myjudge")

        def evaluate(self, ctx):
            ok = ctx.output_data == ctx.expected_output
            return EvaluatorResult(value=ok, assessment="pass" if ok else "fail", reasoning="r")

    r = runner.evaluate_one(MyJudge(), recorded="a", new="b", input_data={})
    assert r["name"] == "myjudge" and r["assessment"] == "fail" and r["reasoning"] == "r"

    def boom(input_data, output_data, expected_output):  # an evaluator error becomes a row, never propagates
        raise ValueError("x")

    r = runner.evaluate_one(boom, recorded=1, new=1, input_data={})
    assert r["error"] and r["assessment"] is None


def test_replay_scores_attached_evaluators_only_when_enabled():
    _set_mode(Mode.CAPTURE)

    def at_least_as_long(input_data, output_data, expected_output):
        return len(output_data) >= len(expected_output)

    @experiment_start(name="e", inputs=["x"], output=lambda r: r, evaluators=lambda: [at_least_as_long])
    def f(x):
        return x

    f("hello")
    _set_mode(Mode.REPLAY)

    assert "evals" not in runner.replay("e", runner.exact)[0]  # off by default
    rows = runner.replay("e", runner.exact, score_evaluators=True)
    assert rows[0]["evals"][0]["name"] == "at_least_as_long" and rows[0]["evals"][0]["assessment"] == "pass"


def test_publish_stacks_user_evaluators_behind_guard(monkeypatch):
    import ddtrace.llmobs as llmobs_pkg
    from ddtrace.llmobs import _inline_experiment_sdk as sdk

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


def test_cli_print_report_counts(capsys):
    rows = [
        {"input": {}, "recorded": 1, "new": 1, "status": "MATCH"},
        {"input": {}, "recorded": 1, "new": 2, "status": "CHANGED"},
    ]
    counts = cli._print_report("e", rows)
    assert counts == {"MATCH": 1, "CHANGED": 1}


def test_cli_print_report_counts_eval_fail(capsys):
    rows = [
        {
            "input": {},
            "recorded": 1,
            "new": 1,
            "status": "MATCH",
            "evals": [{"name": "judge", "value": False, "assessment": "fail", "reasoning": None, "error": None}],
        },
    ]
    counts = cli._print_report("e", rows)
    assert counts["MATCH"] == 1 and counts["EVAL_FAIL"] == 1  # a failing eval is counted for the exit gate
