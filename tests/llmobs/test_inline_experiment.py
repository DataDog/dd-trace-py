import asyncio

import pytest

from ddtrace.llmobs._inline_experiment import Mode
from ddtrace.llmobs._inline_experiment import _ExperimentStop
from ddtrace.llmobs._inline_experiment import _reset
from ddtrace.llmobs._inline_experiment import _set_mode
from ddtrace.llmobs._inline_experiment import _set_trace
from ddtrace.llmobs._inline_experiment import captured_cases
from ddtrace.llmobs._inline_experiment import experiment_end
from ddtrace.llmobs._inline_experiment import experiment_start


@pytest.fixture(autouse=True)
def reset_inline_experiments():
    _reset()
    yield
    _reset()


def test_off_mode_is_pure_passthrough():
    @experiment_start(name="e")
    def f(x):
        return x + 1

    assert f(1) == 2
    assert captured_cases("e") == []  # nothing recorded when inactive


def test_capture_single_function_unit_with_output_extractor():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", output=lambda ret: ret["v"])
    def f(x):
        return {"v": x * 2, "ts": "volatile"}

    assert f(3) == {"v": 6, "ts": "volatile"}  # caller still gets the real return
    assert captured_cases("e") == [{"input": {"x": 3}, "output": 6}]


def test_capture_selective_inputs_excludes_live_infra():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", inputs=["msg"])
    def f(infra, msg):
        return msg.upper()

    f(object(), "hi")  # `infra` is non-serializable live infra; must be excluded
    assert captured_cases("e") == [{"input": {"msg": "hi"}, "output": "HI"}]


def test_capture_emit_shape_records_output_at_end():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e")
    def ingest(q):
        return emit(q.upper())

    @experiment_end(name="e")
    def emit(result):
        return result

    ingest("hi")
    assert captured_cases("e") == [{"input": {"q": "hi"}, "output": "HI"}]


def test_replay_unwinds_at_end_before_side_effects():
    side_effects = []

    @experiment_start(name="e")
    def ingest(q):
        return emit(q)

    @experiment_end(name="e")
    def emit(result):
        side_effects.append(result)  # a real side effect that must NOT run on replay
        return result

    _set_mode(Mode.REPLAY)
    with pytest.raises(_ExperimentStop) as excinfo:
        ingest("x")
    assert excinfo.value.output == "x"
    assert side_effects == []  # stop-point unwound before emit ran


def test_async_capture_single_function_unit():
    _set_mode(Mode.CAPTURE)

    @experiment_start(name="e", output=lambda ret: ret)
    async def f(x):
        await asyncio.sleep(0)
        return x * 2

    assert asyncio.run(f(5)) == 10
    assert captured_cases("e") == [{"input": {"x": 5}, "output": 10}]


def test_broad_except_does_not_swallow_replay_unwind():
    @experiment_start(name="e")
    def ingest(q):
        try:
            return emit(q)
        except Exception:  # noqa: BLE001 - intentionally broad; must not catch the unwind
            return "swallowed"

    @experiment_end(name="e")
    def emit(result):
        return result

    _set_mode(Mode.REPLAY)
    with pytest.raises(_ExperimentStop):
        ingest("x")  # _ExperimentStop is BaseException, so the broad except misses it


# --------------------------------------------------------------------------- #
# --trace: capture also opens an LLM Obs workflow span and links the case to it
# --------------------------------------------------------------------------- #
class _FakeSpan:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeLLMObs:
    """Minimal stand-in for the real LLMObs in trace-linking tests."""

    enabled = True
    annotations: list = []
    workflow_calls = 0

    @classmethod
    def workflow(cls, name=None, **kwargs):
        cls.workflow_calls += 1
        return _FakeSpan()

    @staticmethod
    def export_span(span=None):
        return {"span_id": "S1", "trace_id": "T1"}

    @classmethod
    def annotate(cls, span=None, input_data=None, output_data=None, **kwargs):
        cls.annotations.append({"input_data": input_data, "output_data": output_data})


def test_capture_with_trace_links_case_to_span(monkeypatch):
    import ddtrace.llmobs as llmobs_pkg

    _FakeLLMObs.annotations = []
    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)
    _set_mode(Mode.CAPTURE)
    _set_trace(True)

    @experiment_start(name="e", output=lambda ret: ret)
    def f(x):
        return x * 2

    assert f(5) == 10
    assert captured_cases("e") == [{"input": {"x": 5}, "output": 10, "trace": {"span_id": "S1", "trace_id": "T1"}}]
    # the root workflow span is annotated with the boundary input/output
    assert _FakeLLMObs.annotations == [{"input_data": {"x": 5}, "output_data": 10}]


def test_capture_with_trace_emit_shape_links_case(monkeypatch):
    import ddtrace.llmobs as llmobs_pkg

    _FakeLLMObs.annotations = []
    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)
    _set_mode(Mode.CAPTURE)
    _set_trace(True)

    @experiment_start(name="e", inputs=["q"])
    def handle(q):
        emit(q.upper())
        return "done"

    @experiment_end(name="e", output=lambda args, kwargs: args[0])
    def emit(answer):
        return answer

    handle("hi")
    assert captured_cases("e") == [{"input": {"q": "hi"}, "output": "HI", "trace": {"span_id": "S1", "trace_id": "T1"}}]
    # emit-shape annotates the root span with the input and the emitted output
    assert _FakeLLMObs.annotations == [{"input_data": {"q": "hi"}, "output_data": "HI"}]


def test_capture_with_trace_link_uses_real_span_and_no_wrapper(monkeypatch):
    import ddtrace.llmobs as llmobs_pkg

    _FakeLLMObs.annotations = []
    _FakeLLMObs.workflow_calls = 0
    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)
    _set_mode(Mode.CAPTURE)
    _set_trace(True)

    # boundary already emits its own span and returns its {span_id, trace_id} as ret[1]
    @experiment_start(name="e", output=lambda ret: ret[0], trace_link=lambda ret: ret[1])
    def f(x):
        return (x * 2, {"span_id": "REAL", "trace_id": "RT"})

    assert f(5) == (10, {"span_id": "REAL", "trace_id": "RT"})
    # case links to the REAL span, not a synthesized one
    assert captured_cases("e") == [{"input": {"x": 5}, "output": 10, "trace": {"span_id": "REAL", "trace_id": "RT"}}]
    # no synthetic span was opened, and we did not annotate (the real span's own decorator does)
    assert _FakeLLMObs.workflow_calls == 0
    assert _FakeLLMObs.annotations == []


def test_capture_with_trace_reuses_active_span_without_trace_link(monkeypatch):
    # When a real LLM Obs span is already active (e.g. experiment_start nested inside an
    # @workflow/@agent), --trace reuses it: no synthetic span, no trace_link needed.
    import ddtrace
    import ddtrace.llmobs as llmobs_pkg

    _FakeLLMObs.annotations = []
    _FakeLLMObs.workflow_calls = 0
    monkeypatch.setattr(llmobs_pkg, "LLMObs", _FakeLLMObs)
    monkeypatch.setattr(ddtrace.tracer, "current_span", lambda: object(), raising=False)
    _set_mode(Mode.CAPTURE)
    _set_trace(True)

    @experiment_start(name="e", output=lambda ret: ret)
    def f(x):
        return x * 2

    assert f(5) == 10
    # linked to the reused active span (via export_span), and NO synthetic workflow opened
    assert captured_cases("e") == [{"input": {"x": 5}, "output": 10, "trace": {"span_id": "S1", "trace_id": "T1"}}]
    assert _FakeLLMObs.workflow_calls == 0
    assert _FakeLLMObs.annotations == [{"input_data": {"x": 5}, "output_data": 10}]


def test_capture_with_trace_but_llmobs_disabled_is_safe(monkeypatch):
    class _Disabled:
        enabled = False

    import ddtrace.llmobs as llmobs_pkg

    monkeypatch.setattr(llmobs_pkg, "LLMObs", _Disabled)
    _set_mode(Mode.CAPTURE)
    _set_trace(True)  # requested, but LLM Obs is off -> capture still works, no trace key

    @experiment_start(name="e", output=lambda ret: ret)
    def f(x):
        return x + 1

    assert f(2) == 3
    assert captured_cases("e") == [{"input": {"x": 2}, "output": 3}]
