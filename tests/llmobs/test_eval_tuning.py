"""Tests for the eval tuning SDK components.

Tests the metrics evaluators, config classes, dataset splitting, and
JudgeConfig prompt building — all without requiring API calls or Docker.
"""

import math

from unittest.mock import patch

import pytest

from ddtrace.llmobs._eval_tuning import JudgeConfig
from ddtrace.llmobs._eval_tuning import PromptVersion
from ddtrace.llmobs._eval_tuning import TrainingExample
from ddtrace.llmobs._eval_tuning import split_dataset
from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.metrics import AlignmentConfig
from ddtrace.llmobs._evaluators.metrics import BiasEvaluator
from ddtrace.llmobs._evaluators.metrics import ClassConfig
from ddtrace.llmobs._evaluators.metrics import compute_consistency
from ddtrace.llmobs._evaluators.metrics import CorrelationEvaluator
from ddtrace.llmobs._evaluators.metrics import DisagreementEvaluator
from ddtrace.llmobs._evaluators.metrics import MAEEvaluator
from ddtrace.llmobs._evaluators.metrics import RMSEEvaluator
from ddtrace.llmobs._evaluators.metrics import TNREvaluator
from ddtrace.llmobs._evaluators.metrics import TPREvaluator
from ddtrace.llmobs._experiment import SummaryEvaluatorContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_summary_context(judge_name, judge_scores, human_scores, score_key=None, human_key=None):
    """Build a SummaryEvaluatorContext from parallel lists of judge and human scores."""
    if score_key:
        eval_results = {judge_name: [{score_key: s} for s in judge_scores]}
    else:
        eval_results = {judge_name: judge_scores}

    if human_key:
        expected = [{human_key: s} for s in human_scores]
    else:
        expected = human_scores

    n = max(len(judge_scores), len(human_scores))
    return SummaryEvaluatorContext(
        inputs=[{"text": f"input-{i}"} for i in range(n)],
        outputs=[f"output-{i}" for i in range(n)],
        evaluation_results=eval_results,
        expected_outputs=expected,
    )


def _make_dataset_record(record_id, input_data=None, expected_output=None, metadata=None):
    """Build a minimal DatasetRecord TypedDict."""
    return {
        "record_id": record_id,
        "input_data": input_data or {"text": f"record-{record_id}"},
        "expected_output": expected_output or 5.0,
        "metadata": metadata or {},
    }


# ===========================================================================
# ClassConfig tests
# ===========================================================================


class TestClassConfig:
    def test_score_classify_pass(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        assert cc.classify(0.7) is True
        assert cc.classify(0.5) is True

    def test_score_classify_fail(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        assert cc.classify(0.3) is False

    def test_score_classify_none_for_non_numeric(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        assert cc.classify("hello") is None
        assert cc.classify(None) is None

    def test_score_classify_excludes_booleans(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        assert cc.classify(True) is None
        assert cc.classify(False) is None

    def test_boolean_classify(self):
        cc = ClassConfig(output_type="boolean", pass_when=True)
        assert cc.classify(True) is True
        assert cc.classify(False) is False

    def test_categorical_classify(self):
        cc = ClassConfig(output_type="categorical", pass_values=["good", "great"])
        assert cc.classify("good") is True
        assert cc.classify("great") is True
        assert cc.classify("bad") is False

    def test_invalid_output_type(self):
        with pytest.raises(ValueError, match="output_type must be"):
            ClassConfig(output_type="invalid")

    def test_missing_thresholds(self):
        with pytest.raises(ValueError, match="pass_thresholds required"):
            ClassConfig(output_type="score")

    def test_serialization_roundtrip(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.7])
        restored = ClassConfig.from_dict(cc.to_dict())
        assert restored.output_type == "score"
        assert restored.pass_thresholds == [0.7]


# ===========================================================================
# AlignmentConfig tests
# ===========================================================================


class TestAlignmentConfig:
    def test_score_good(self):
        ac = AlignmentConfig(output_type="score", alignment_thresholds=[10, 20])
        assert ac.classify_disagreement(50, 55) == "good"  # delta=5 < 10

    def test_score_ok(self):
        ac = AlignmentConfig(output_type="score", alignment_thresholds=[10, 20])
        assert ac.classify_disagreement(50, 65) == "ok"  # delta=15, 10<=15<20

    def test_score_bad(self):
        ac = AlignmentConfig(output_type="score", alignment_thresholds=[10, 20])
        assert ac.classify_disagreement(50, 80) == "bad"  # delta=30 >= 20

    def test_boolean_agreement(self):
        ac = AlignmentConfig(output_type="boolean")
        assert ac.classify_disagreement(True, True) == "good"
        assert ac.classify_disagreement(False, False) == "good"

    def test_boolean_disagreement(self):
        ac = AlignmentConfig(output_type="boolean")
        assert ac.classify_disagreement(True, False) == "bad"

    def test_categorical_match(self):
        ac = AlignmentConfig(output_type="categorical")
        assert ac.classify_disagreement("positive", "positive") == "good"

    def test_categorical_mismatch(self):
        ac = AlignmentConfig(output_type="categorical")
        assert ac.classify_disagreement("positive", "negative") == "bad"

    def test_score_excludes_booleans(self):
        ac = AlignmentConfig(output_type="score", alignment_thresholds=[10])
        assert ac.classify_disagreement(True, False) is None

    def test_custom_labels(self):
        ac = AlignmentConfig(
            output_type="score", alignment_thresholds=[5], class_labels=["excellent", "poor"]
        )
        assert ac.classify_disagreement(10, 12) == "excellent"
        assert ac.classify_disagreement(10, 20) == "poor"

    def test_serialization_roundtrip(self):
        ac = AlignmentConfig(output_type="score", alignment_thresholds=[10, 20])
        restored = AlignmentConfig.from_dict(ac.to_dict())
        assert restored.alignment_thresholds == [10, 20]
        assert restored.class_labels == ["good", "ok", "bad"]


# ===========================================================================
# Metrics evaluator tests
# ===========================================================================


class TestMAEEvaluator:
    def test_basic(self):
        ctx = _make_summary_context("judge", [3.0, 5.0, 7.0], [4.0, 5.0, 9.0])
        result = MAEEvaluator("judge").evaluate(ctx)
        # |3-4| + |5-5| + |7-9| = 1+0+2 = 3, MAE = 1.0
        assert result["value"] == pytest.approx(1.0)
        assert result["count"] == 3

    def test_empty(self):
        ctx = _make_summary_context("judge", [], [])
        result = MAEEvaluator("judge").evaluate(ctx)
        assert result["value"] is None
        assert result["count"] == 0

    def test_with_score_key(self):
        ctx = _make_summary_context("judge", [3.0, 5.0], [4.0, 5.0], score_key="score")
        result = MAEEvaluator("judge", score_key="score").evaluate(ctx)
        assert result["value"] == pytest.approx(0.5)


class TestRMSEEvaluator:
    def test_basic(self):
        ctx = _make_summary_context("judge", [3.0, 5.0, 7.0], [4.0, 5.0, 9.0])
        result = RMSEEvaluator("judge").evaluate(ctx)
        # (1+0+4)/3 = 5/3, sqrt(5/3) ≈ 1.291
        assert result["value"] == pytest.approx(math.sqrt(5.0 / 3.0))
        assert result["count"] == 3


class TestBiasEvaluator:
    def test_positive_bias(self):
        # judge systematically scores 2 higher
        ctx = _make_summary_context("judge", [7.0, 8.0, 9.0], [5.0, 6.0, 7.0])
        result = BiasEvaluator("judge").evaluate(ctx)
        assert result["value"] == pytest.approx(2.0)

    def test_negative_bias(self):
        ctx = _make_summary_context("judge", [3.0, 4.0], [5.0, 6.0])
        result = BiasEvaluator("judge").evaluate(ctx)
        assert result["value"] == pytest.approx(-2.0)

    def test_no_bias(self):
        ctx = _make_summary_context("judge", [5.0, 5.0], [5.0, 5.0])
        result = BiasEvaluator("judge").evaluate(ctx)
        assert result["value"] == pytest.approx(0.0)


class TestCorrelationEvaluator:
    def test_perfect_correlation(self):
        ctx = _make_summary_context("judge", [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        result = CorrelationEvaluator("judge").evaluate(ctx)
        assert result["value"] == pytest.approx(1.0)

    def test_negative_correlation(self):
        ctx = _make_summary_context("judge", [1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0])
        result = CorrelationEvaluator("judge").evaluate(ctx)
        assert result["value"] == pytest.approx(-1.0)

    def test_insufficient_data(self):
        ctx = _make_summary_context("judge", [5.0], [5.0])
        result = CorrelationEvaluator("judge").evaluate(ctx)
        assert result["value"] is None

    def test_constant_values(self):
        ctx = _make_summary_context("judge", [5.0, 5.0, 5.0], [5.0, 5.0, 5.0])
        result = CorrelationEvaluator("judge").evaluate(ctx)
        assert result["value"] is None  # std dev is 0


class TestTPREvaluator:
    def test_perfect_tpr(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        ctx = _make_summary_context("judge", [0.8, 0.9, 0.2], [0.7, 0.6, 0.3])
        result = TPREvaluator("judge", cc).evaluate(ctx)
        # human passes: indices 0, 1 (>=0.5). judge also passes both. TPR=1.0
        assert result["value"] == pytest.approx(1.0)

    def test_low_tpr(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        ctx = _make_summary_context("judge", [0.3, 0.3, 0.8], [0.7, 0.6, 0.3])
        result = TPREvaluator("judge", cc).evaluate(ctx)
        # human passes: indices 0, 1. judge passes neither (0.3 < 0.5). TPR=0.0
        assert result["value"] == pytest.approx(0.0)

    def test_no_positives(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        ctx = _make_summary_context("judge", [0.3, 0.2], [0.3, 0.2])
        result = TPREvaluator("judge", cc).evaluate(ctx)
        # no human passes, so TPR is undefined
        assert result["value"] is None


class TestTNREvaluator:
    def test_perfect_tnr(self):
        cc = ClassConfig(output_type="score", pass_thresholds=[0.5])
        ctx = _make_summary_context("judge", [0.8, 0.2, 0.1], [0.7, 0.3, 0.1])
        result = TNREvaluator("judge", cc).evaluate(ctx)
        # human fails: indices 1, 2. judge also fails both. TNR=1.0
        assert result["value"] == pytest.approx(1.0)


class TestDisagreementEvaluator:
    def test_score_disagreement(self):
        ac = AlignmentConfig(output_type="score", alignment_thresholds=[10, 20])
        ctx = _make_summary_context("judge", [50, 65, 90], [55, 50, 50])
        result = DisagreementEvaluator("judge", ac).evaluate(ctx)
        # deltas: 5 (good), 15 (ok), 40 (bad)
        assert result["counts"]["good"] == 1
        assert result["counts"]["ok"] == 1
        assert result["counts"]["bad"] == 1
        assert result["total"] == 3


# ===========================================================================
# compute_consistency tests
# ===========================================================================


class _MockRun:
    """Minimal mock for ExperimentRun with a rows attribute."""

    def __init__(self, rows):
        self.rows = rows


class TestComputeConsistency:
    def test_single_run_returns_zero(self):
        result = compute_consistency({"runs": [_MockRun([])]})
        assert result["value"] == 0.0
        assert result["count"] == 0

    def test_no_runs(self):
        result = compute_consistency({})
        assert result["value"] == 0.0

    def test_consistent_scores(self):
        """When judge gives same score across runs, stddev should be 0."""
        runs = [
            _MockRun([{"record_id": "r1", "evaluations": {"judge": {"value": 5.0}}}]),
            _MockRun([{"record_id": "r1", "evaluations": {"judge": {"value": 5.0}}}]),
        ]
        result = compute_consistency({"runs": runs})
        assert result["value"] == pytest.approx(0.0)
        assert result["count"] == 1

    def test_inconsistent_scores(self):
        """When judge gives different scores, stddev should be nonzero."""
        runs = [
            _MockRun([{"record_id": "r1", "evaluations": {"judge": {"value": 3.0}}}]),
            _MockRun([{"record_id": "r1", "evaluations": {"judge": {"value": 7.0}}}]),
        ]
        result = compute_consistency({"runs": runs})
        # stddev of [3, 7] = 2.0
        assert result["value"] == pytest.approx(2.0)
        assert result["per_scenario"]["r1"] == pytest.approx(2.0)

    def test_multiple_scenarios(self):
        runs = [
            _MockRun([
                {"record_id": "r1", "evaluations": {"judge": {"value": 4.0}}},
                {"record_id": "r2", "evaluations": {"judge": {"value": 10.0}}},
            ]),
            _MockRun([
                {"record_id": "r1", "evaluations": {"judge": {"value": 6.0}}},
                {"record_id": "r2", "evaluations": {"judge": {"value": 10.0}}},
            ]),
        ]
        result = compute_consistency({"runs": runs})
        # r1: stddev([4,6]) = 1.0, r2: stddev([10,10]) = 0.0, avg = 0.5
        assert result["value"] == pytest.approx(0.5)
        assert result["count"] == 2


# ===========================================================================
# JudgeConfig tests
# ===========================================================================


class TestJudgeConfig:
    def _make_config(self, prompt="Rate this: {{output_data}}", training_examples=None):
        return JudgeConfig(
            id="jc-1",
            project_id="proj-1",
            model="gpt-4o",
            provider="openai",
            prompt=PromptVersion(id="pv-1", version=1, content=prompt),
            structured_output=ScoreStructuredOutput(
                description="quality", min_score=1, max_score=10
            ),
            training_examples=training_examples or [],
        )

    @patch("ddtrace.llmobs._evaluators.llm_judge._create_openai_client", return_value=None)
    def test_to_llm_judge_basic(self, _mock_client):
        config = self._make_config()
        judge = config.to_llm_judge(name="my_judge")
        assert judge.name == "my_judge"

    def test_prompt_without_examples(self):
        config = self._make_config(prompt="Rate this: {{output_data}}")
        prompt = config._build_prompt_with_examples()
        assert "Training Examples" not in prompt
        assert prompt == "Rate this: {{output_data}}"

    def test_prompt_with_examples(self):
        examples = [
            TrainingExample(id="ex-1", record_id="r1", input_data={"text": "hello"}, expected_output=8),
            TrainingExample(id="ex-2", record_id="r2", input_data={"text": "bye"}, expected_output=3),
        ]
        config = self._make_config(training_examples=examples)
        prompt = config._build_prompt_with_examples()
        assert "Training Examples" in prompt
        assert "Example 1" in prompt
        assert "Example 2" in prompt
        assert "Expected Score/Label: 8" in prompt
        assert "Expected Score/Label: 3" in prompt

    def test_serialization_roundtrip(self):
        config = self._make_config()
        d = config.to_dict()
        assert d["model"] == "gpt-4o"
        assert d["provider"] == "openai"
        assert d["structured_output_type"] == "score"
        assert d["structured_output_config"]["min_score"] == 1
        assert d["structured_output_config"]["max_score"] == 10

    def test_boolean_output_serialization(self):
        config = JudgeConfig(
            id="jc-2",
            project_id="proj-1",
            model="gpt-4o",
            provider="openai",
            prompt=PromptVersion(id="pv-1", version=1, content="Is this good?"),
            structured_output=BooleanStructuredOutput(description="quality check"),
        )
        d = config.to_dict()
        assert d["structured_output_type"] == "boolean"

    def test_categorical_output_serialization(self):
        config = JudgeConfig(
            id="jc-3",
            project_id="proj-1",
            model="gpt-4o",
            provider="openai",
            prompt=PromptVersion(id="pv-1", version=1, content="Classify this"),
            structured_output=CategoricalStructuredOutput(
                categories=["positive", "negative", "neutral"]
            ),
        )
        d = config.to_dict()
        assert d["structured_output_type"] == "categorical"


# ===========================================================================
# Dataset splitting tests
# ===========================================================================


class _FakeDataset:
    """Minimal Dataset stand-in for split_dataset testing."""

    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def __len__(self):
        return len(self._records)

    @property
    def records(self):
        return self._records


class TestSplitDataset:
    def test_basic_split(self):
        records = [_make_dataset_record(str(i)) for i in range(100)]
        ds = _FakeDataset(records)
        train, dev, test = split_dataset(ds, 0.7, 0.15, 0.15)
        assert len(train) + len(dev) + len(test) == 100
        # Check approximate ratios (exact counts depend on rounding)
        assert 60 <= len(train) <= 80
        assert 10 <= len(dev) <= 25
        assert 10 <= len(test) <= 25

    def test_all_records_present(self):
        records = [_make_dataset_record(str(i)) for i in range(50)]
        ds = _FakeDataset(records)
        train, dev, test = split_dataset(ds, 0.6, 0.2, 0.2)
        all_ids = {r["record_id"] for r in train + dev + test}
        expected_ids = {str(i) for i in range(50)}
        assert all_ids == expected_ids

    def test_no_overlap(self):
        records = [_make_dataset_record(str(i)) for i in range(50)]
        ds = _FakeDataset(records)
        train, dev, test = split_dataset(ds, 0.6, 0.2, 0.2)
        train_ids = {r["record_id"] for r in train}
        dev_ids = {r["record_id"] for r in dev}
        test_ids = {r["record_id"] for r in test}
        assert len(train_ids & dev_ids) == 0
        assert len(train_ids & test_ids) == 0
        assert len(dev_ids & test_ids) == 0

    def test_deterministic_with_seed(self):
        records = [_make_dataset_record(str(i)) for i in range(50)]
        ds = _FakeDataset(records)
        train1, dev1, test1 = split_dataset(ds, 0.6, 0.2, 0.2, seed=123)
        train2, dev2, test2 = split_dataset(ds, 0.6, 0.2, 0.2, seed=123)
        assert [r["record_id"] for r in train1] == [r["record_id"] for r in train2]
        assert [r["record_id"] for r in dev1] == [r["record_id"] for r in dev2]

    def test_group_column_keeps_groups_together(self):
        records = []
        for i in range(100):
            group = f"group_{i % 10}"  # 10 groups of 10 records each
            records.append(_make_dataset_record(str(i), metadata={"topic": group}))
        ds = _FakeDataset(records)
        train, dev, test = split_dataset(ds, 0.6, 0.2, 0.2, group_column="topic")

        # All records accounted for
        assert len(train) + len(dev) + len(test) == 100

        # Verify each group appears in exactly one split
        all_splits = {"train": train, "dev": dev, "test": test}
        for group_id in range(10):
            group_name = f"group_{group_id}"
            containing_splits = []
            for split_name, split in all_splits.items():
                if any(r["metadata"]["topic"] == group_name for r in split):
                    containing_splits.append(split_name)
            assert len(containing_splits) == 1, (
                f"Group {group_name} found in {len(containing_splits)} splits: {containing_splits}"
            )

    def test_ratios_must_sum_to_one(self):
        records = [_make_dataset_record(str(i)) for i in range(10)]
        ds = _FakeDataset(records)
        with pytest.raises(ValueError):
            split_dataset(ds, 0.5, 0.2, 0.1)  # sums to 0.8

    def test_small_dataset(self):
        records = [_make_dataset_record(str(i)) for i in range(3)]
        ds = _FakeDataset(records)
        train, dev, test = split_dataset(ds, 0.6, 0.2, 0.2)
        assert len(train) + len(dev) + len(test) == 3
