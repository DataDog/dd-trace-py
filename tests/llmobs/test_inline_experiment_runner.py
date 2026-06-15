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


# --- CLI helpers ----------------------------------------------------------- #
def test_cli_arg_parser():
    p = cli._build_arg_parser()
    a = p.parse_args(["run", "myapp:gen", "--ignore", "a,b"])
    assert a.command == "run" and a.target == "myapp:gen" and a.ignore == "a,b"
    assert p.parse_args(["list", "myapp"]).command == "list"


def test_cli_print_report_counts(capsys):
    rows = [
        {"input": {}, "recorded": 1, "new": 1, "status": "MATCH"},
        {"input": {}, "recorded": 1, "new": 2, "status": "CHANGED"},
    ]
    counts = cli._print_report("e", rows)
    assert counts == {"MATCH": 1, "CHANGED": 1}
