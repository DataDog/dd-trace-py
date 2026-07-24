from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from unittest.mock import Mock

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.eval import record_eval_metric
from tests.contrib.patch import emit_integration_and_version_to_test_agent


_CAPTURE_PATH_ENV = "_DD_PYTEST_DEEPEVAL_CAPTURE_PATH"


# AIDEV-NOTE: This plugin installs mocks at import time so they are active before the child process
# initializes the Datadog pytest plugin (mirrors tests/testing/internal/pytest/test_pytest_bdd.py).
_INFRA_PLUGIN = f"""\
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


_api_client_patch = patch(
    "ddtrace.testing.internal.session_manager.APIClient",
    return_value=mock_api_client_settings(),
)
_api_client_patch.start()
_standard_mocks = setup_standard_mocks()
_standard_mocks.__enter__()
_event_capture_context = EventCapture.capture()
_event_capture = _event_capture_context.__enter__()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session):
    tests = []
    for event in _event_capture.events():
        if event["type"] != "test":
            continue
        content = event["content"]
        tests.append(
            {{
                "meta": content.get("meta", {{}}),
                "metrics": content.get("metrics", {{}}),
            }}
        )
    Path(os.environ["{_CAPTURE_PATH_ENV}"]).write_text(json.dumps(tests))
"""


# A deepeval metric that returns a fixed score without calling an LLM.
#
# NOTE: deepeval's copy_metrics() (deepeval/metrics/utils.py) clones a metric
# before the async run via ``type(metric)(**{init-params present in vars(metric)})``.
# It only forwards attributes named exactly like the __init__ parameters, so the
# intended values must be stored on ``self.score``/``self.name``/``self.error``
# (not private aliases) to survive copying.
_FAKE_METRIC = """
from deepeval.metrics import BaseMetric


class FakeMetric(BaseMetric):
    _required_params = []

    def __init__(self, threshold=0.5, score=0.9, name="Answer Relevancy", error=None):
        self.threshold = threshold
        self.score = score
        self.name = name
        self.error = error
        self.evaluation_model = "fake-model"
        self.evaluation_cost = 0.0001
        self.include_reason = True
        self.async_mode = True
        self.strict_mode = False
        self.reason = None
        self.success = None

    def measure(self, test_case, *args, **kwargs):
        if self.error is not None:
            self.success = False
            self.score = None
            return 0.0
        self.reason = "fake reason"
        self.success = self.score >= self.threshold
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self):
        if self.error is not None:
            return False
        return bool(self.success)

    @property
    def __name__(self):
        return self.name
"""


def _run_deepeval_subprocess(pytester: Pytester, file_name: str):
    capture_path = pytester.path / "deepeval_capture.json"
    pytester.makepyfile(deepeval_infra=_INFRA_PLUGIN)
    pytester.makepyfile(fake_deepeval_metric=_FAKE_METRIC)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv(_CAPTURE_PATH_ENV, str(capture_path))
        # Keep deepeval from making any network calls when importing / running under plain pytest.
        monkeypatch.setenv("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
        monkeypatch.delenv("CONFIDENT_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # The deepeval integration enriches the real ddtrace ``type=test`` root span, which only exists when the
        # trace filter is active. dd-trace-py's own riot venvs set ``_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER=1``
        # (which disables that span); unset it so the inner run behaves like a real user's ``pytest --ddtrace``.
        monkeypatch.setenv("_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER", "false")
        result = pytester.runpytest_subprocess("-p", "no:randomly", "--ddtrace", "-p", "deepeval_infra", file_name)

    return result, json.loads(capture_path.read_text())


def _run_deepeval_cli(pytester: Pytester, file_name: str):
    """Run the tests via the ``deepeval test run`` CLI instead of plain pytest.

    ``deepeval test run`` executes pytest in-process (``pytest.main``) with
    ``get_is_running_deepeval()`` True, and forwards extra args (``allow_extra_args``),
    so ``--ddtrace`` still activates Test Optimization. This validates the integration
    under deepeval's own CLI entry point.
    """
    capture_path = pytester.path / "deepeval_capture.json"
    pytester.makepyfile(deepeval_infra=_INFRA_PLUGIN)
    pytester.makepyfile(fake_deepeval_metric=_FAKE_METRIC)

    deepeval_bin = shutil.which("deepeval") or os.path.join(os.path.dirname(sys.executable), "deepeval")

    env = dict(os.environ)
    env[_CAPTURE_PATH_ENV] = str(capture_path)
    env["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
    env["_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER"] = "false"
    env["PYTHONPATH"] = str(pytester.path) + os.pathsep + env.get("PYTHONPATH", "")
    for var in ("CONFIDENT_API_KEY", "OPENAI_API_KEY", "PYTEST_XDIST_WORKER", "PYTEST_XDIST_TESTRUNUID"):
        env.pop(var, None)

    proc = subprocess.run(
        [deepeval_bin, "test", "run", file_name, "--ddtrace", "-p", "deepeval_infra", "-p", "no:randomly"],
        cwd=str(pytester.path),
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, json.loads(capture_path.read_text())


class _FakeSpan:
    """Minimal span stand-in exposing the get/set tag/metric API used by record_eval_metric."""

    def __init__(self) -> None:
        self._tags: dict = {}
        self._metrics: dict = {}

    def set_tag(self, key, value=None) -> None:
        self._tags[key] = value

    def get_tag(self, key):
        return self._tags.get(key)

    def set_metric(self, key, value) -> None:
        self._metrics[key] = value

    def get_metric(self, key):
        return self._metrics.get(key)


class TestEvalSchema:
    """Unit tests for the generic eval.* schema helper (no deepeval / pytest plumbing)."""

    def test_records_all_fields(self) -> None:
        span = _FakeSpan()
        slug = record_eval_metric(
            span,
            framework="deepeval",
            name="Answer Relevancy",
            score=0.9,
            threshold=0.5,
            passed=True,
            model="gpt-4o",
            cost=0.0002,
            reason="looks good",
        )
        assert slug == "answer_relevancy"
        assert span.get_tag("test.type") == "eval"
        assert span.get_tag("eval.framework") == "deepeval"
        assert span.get_tag("eval.answer_relevancy.name") == "Answer Relevancy"
        assert span.get_tag("eval.answer_relevancy.status") == "pass"
        assert span.get_tag("eval.answer_relevancy.model") == "gpt-4o"
        assert span.get_tag("eval.answer_relevancy.reason") == "looks good"
        assert span.get_metric("eval.answer_relevancy.score") == 0.9
        assert span.get_metric("eval.answer_relevancy.threshold") == 0.5
        assert span.get_metric("eval.answer_relevancy.passed") == 1.0
        assert span.get_metric("eval.answer_relevancy.cost") == 0.0002
        assert span.get_metric("eval.metric_count") == 1
        assert span.get_tag("eval.metrics") == "answer_relevancy"

    def test_records_token_usage(self) -> None:
        span = _FakeSpan()
        record_eval_metric(
            span,
            framework="ragas",
            name="faithfulness",
            score=1.0,
            threshold=0.8,
            passed=True,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )
        assert span.get_metric("eval.faithfulness.tokens.input") == 100
        assert span.get_metric("eval.faithfulness.tokens.output") == 20
        assert span.get_metric("eval.faithfulness.tokens.total") == 120

    def test_dedupes_repeated_metric_names(self) -> None:
        span = _FakeSpan()
        first = record_eval_metric(span, framework="deepeval", name="Relevancy", score=0.9, passed=True)
        second = record_eval_metric(span, framework="deepeval", name="Relevancy", score=0.4, passed=False)
        assert first == "relevancy"
        assert second == "relevancy_2"
        assert span.get_metric("eval.metric_count") == 2
        assert span.get_tag("eval.metrics") == "relevancy,relevancy_2"

    def test_errored_metric(self) -> None:
        span = _FakeSpan()
        record_eval_metric(span, framework="deepeval", name="Relevancy", error="boom", passed=False)
        assert span.get_tag("eval.relevancy.status") == "error"
        assert span.get_tag("eval.relevancy.error") == "boom"
        assert span.get_metric("eval.relevancy.score") is None

    def test_none_span_is_noop(self) -> None:
        assert record_eval_metric(None, framework="deepeval", name="x", score=1.0) is None


class TestDeepevalPatch:
    def test_test_root_span_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.contrib.internal.deepeval import patch as deepeval_patch

        fake_tracer = Mock()
        monkeypatch.setattr(deepeval_patch, "tracer", fake_tracer)

        non_test_span = Mock()
        non_test_span.get_tag.return_value = "suite"
        fake_tracer.current_root_span.return_value = non_test_span
        assert deepeval_patch._test_root_span() is None

        test_span = Mock()
        test_span.get_tag.return_value = "test"
        fake_tracer.current_root_span.return_value = test_span
        assert deepeval_patch._test_root_span() is test_span

        fake_tracer.current_root_span.return_value = None
        assert deepeval_patch._test_root_span() is None

    @pytest.mark.xfail(raises=ConnectionRefusedError, reason="test agent is down")
    def test_and_emit_get_version(self) -> None:
        from ddtrace.contrib.internal.deepeval.patch import get_version

        version = get_version()
        assert isinstance(version, str)
        assert version != ""
        emit_integration_and_version_to_test_agent("deepeval", version)


class TestDeepevalTestOptimization:
    @pytest.fixture(autouse=True)
    def clear_xdist_worker_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        monkeypatch.delenv("PYTEST_XDIST_TESTRUNUID", raising=False)

    def test_passing_metric(self, pytester: Pytester) -> None:
        py_file = pytester.makepyfile(
            """
            from deepeval import assert_test
            from deepeval.test_case import LLMTestCase
            from fake_deepeval_metric import FakeMetric

            def test_relevancy():
                assert_test(
                    LLMTestCase(input="What is 2 + 2?", actual_output="4"),
                    [FakeMetric(score=0.9, threshold=0.5)],
                )
            """
        )
        result, tests = _run_deepeval_subprocess(pytester, os.path.basename(str(py_file)))

        result.assert_outcomes(passed=1)
        assert len(tests) == 1
        meta, metrics = tests[0]["meta"], tests[0]["metrics"]
        assert meta["test.type"] == "eval"
        assert meta["eval.framework"] == "deepeval"
        assert meta["eval.answer_relevancy.name"] == "Answer Relevancy"
        assert meta["eval.answer_relevancy.status"] == "pass"
        assert meta["eval.answer_relevancy.reason"] == "fake reason"
        assert meta["eval.answer_relevancy.model"] == "fake-model"
        assert metrics["eval.answer_relevancy.score"] == 0.9
        assert metrics["eval.answer_relevancy.threshold"] == 0.5
        assert metrics["eval.answer_relevancy.passed"] == 1
        assert metrics["eval.answer_relevancy.cost"] == 0.0001
        assert metrics["eval.metric_count"] == 1

    def test_failing_metric_is_still_captured(self, pytester: Pytester) -> None:
        """A metric below threshold makes assert_test raise, but the eval data is captured first."""
        py_file = pytester.makepyfile(
            """
            from deepeval import assert_test
            from deepeval.test_case import LLMTestCase
            from fake_deepeval_metric import FakeMetric

            def test_relevancy():
                assert_test(
                    LLMTestCase(input="What is 2 + 2?", actual_output="5"),
                    [FakeMetric(score=0.1, threshold=0.5)],
                )
            """
        )
        result, tests = _run_deepeval_subprocess(pytester, os.path.basename(str(py_file)))

        result.assert_outcomes(failed=1)
        assert len(tests) == 1
        meta, metrics = tests[0]["meta"], tests[0]["metrics"]
        assert meta["test.status"] == "fail"
        assert meta["test.type"] == "eval"
        assert meta["eval.answer_relevancy.status"] == "fail"
        assert metrics["eval.answer_relevancy.score"] == 0.1
        assert metrics["eval.answer_relevancy.passed"] == 0

    def test_multiple_metrics(self, pytester: Pytester) -> None:
        py_file = pytester.makepyfile(
            """
            from deepeval import assert_test
            from deepeval.test_case import LLMTestCase
            from fake_deepeval_metric import FakeMetric

            def test_multi():
                assert_test(
                    LLMTestCase(input="q", actual_output="a"),
                    [
                        FakeMetric(score=0.9, threshold=0.5, name="Answer Relevancy"),
                        FakeMetric(score=0.8, threshold=0.5, name="Faithfulness"),
                    ],
                )
            """
        )
        result, tests = _run_deepeval_subprocess(pytester, os.path.basename(str(py_file)))

        result.assert_outcomes(passed=1)
        meta, metrics = tests[0]["meta"], tests[0]["metrics"]
        assert metrics["eval.metric_count"] == 2
        assert meta["eval.answer_relevancy.status"] == "pass"
        assert meta["eval.faithfulness.status"] == "pass"
        assert set(meta["eval.metrics"].split(",")) == {"answer_relevancy", "faithfulness"}

    def test_duplicate_metric_name_is_deduped(self, pytester: Pytester) -> None:
        py_file = pytester.makepyfile(
            """
            from deepeval import assert_test
            from deepeval.test_case import LLMTestCase
            from fake_deepeval_metric import FakeMetric

            def test_dup():
                assert_test(
                    LLMTestCase(input="q", actual_output="a"),
                    [
                        FakeMetric(score=0.9, threshold=0.5, name="Answer Relevancy"),
                        FakeMetric(score=0.7, threshold=0.5, name="Answer Relevancy"),
                    ],
                )
            """
        )
        result, tests = _run_deepeval_subprocess(pytester, os.path.basename(str(py_file)))

        result.assert_outcomes(passed=1)
        meta, metrics = tests[0]["meta"], tests[0]["metrics"]
        assert metrics["eval.metric_count"] == 2
        assert meta["eval.answer_relevancy.name"] == "Answer Relevancy"
        assert meta["eval.answer_relevancy_2.name"] == "Answer Relevancy"

    def test_evaluate_path(self, pytester: Pytester) -> None:
        py_file = pytester.makepyfile(
            """
            from deepeval import evaluate
            from deepeval.test_case import LLMTestCase
            from fake_deepeval_metric import FakeMetric

            def test_evaluate():
                evaluate(
                    test_cases=[LLMTestCase(input="q", actual_output="a")],
                    metrics=[FakeMetric(score=0.9, threshold=0.5)],
                )
            """
        )
        result, tests = _run_deepeval_subprocess(pytester, os.path.basename(str(py_file)))

        result.assert_outcomes(passed=1)
        meta, metrics = tests[0]["meta"], tests[0]["metrics"]
        assert meta["test.type"] == "eval"
        assert meta["eval.answer_relevancy.status"] == "pass"
        assert metrics["eval.answer_relevancy.score"] == 0.9

    def test_deepeval_test_run_cli(self, pytester: Pytester) -> None:
        """Eval metrics are captured when tests run via the ``deepeval test run`` CLI, not just pytest."""
        py_file = pytester.makepyfile(
            """
            from deepeval import assert_test
            from deepeval.test_case import LLMTestCase
            from fake_deepeval_metric import FakeMetric

            def test_relevancy():
                assert_test(
                    LLMTestCase(input="What is 2 + 2?", actual_output="4"),
                    [FakeMetric(score=0.9, threshold=0.5)],
                )
            """
        )
        proc, tests = _run_deepeval_cli(pytester, os.path.basename(str(py_file)))

        assert proc.returncode == 0, f"deepeval test run failed:\n{proc.stdout}\n{proc.stderr}"
        assert len(tests) == 1
        meta, metrics = tests[0]["meta"], tests[0]["metrics"]
        assert meta["test.type"] == "eval"
        assert meta["eval.framework"] == "deepeval"
        assert meta["eval.answer_relevancy.status"] == "pass"
        assert metrics["eval.answer_relevancy.score"] == 0.9
        assert metrics["eval.metric_count"] == 1
