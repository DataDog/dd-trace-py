import asyncio

import pytest

from ddtrace.llmobs._inline_experiment import Mode
from ddtrace.llmobs._inline_experiment import _ExperimentStop
from ddtrace.llmobs._inline_experiment import _reset
from ddtrace.llmobs._inline_experiment import _set_mode
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
