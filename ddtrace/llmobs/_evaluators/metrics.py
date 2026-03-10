"""Statistical metrics evaluators for eval tuning.

These BaseSummaryEvaluator subclasses compare judge scores against human labels
to measure judge quality. They are used in the eval tuning loop to track how
well a judge agrees with human annotations across iterations.
"""

import math
from typing import Any
from typing import Optional

from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import SummaryEvaluatorContext
from ddtrace.llmobs.types import JSONType


def _extract_score_pairs(
    context: SummaryEvaluatorContext,
    judge_name: str,
    score_key: Optional[str] = None,
    human_score_key: Optional[str] = None,
) -> list[tuple[float, float]]:
    """Extract paired (judge_score, human_score) values from context.

    :param context: Summary evaluator context with evaluation_results and expected_outputs.
    :param judge_name: Name of the judge evaluator in evaluation_results.
    :param score_key: Key to extract score from judge result dicts. If None, treats result as a scalar.
    :param human_score_key: Key to extract score from expected_output dicts. If None, treats as scalar.
    :return: List of (judge_score, human_score) tuples where both values are valid floats.
    """
    judge_results = context.evaluation_results.get(judge_name, [])
    human_labels = context.expected_outputs

    pairs = []
    for judge_result, human_label in zip(judge_results, human_labels):
        judge_score = _extract_numeric(judge_result, score_key)
        human_score = _extract_numeric(human_label, human_score_key)
        if judge_score is not None and human_score is not None:
            pairs.append((judge_score, human_score))
    return pairs


def _extract_numeric(value: Any, key: Optional[str] = None) -> Optional[float]:
    """Extract a numeric value from a scalar or dict.

    :param value: A scalar number or a dict containing the score.
    :param key: If provided, extract value[key] from a dict.
    :return: Float value, or None if extraction fails.
    """
    if value is None:
        return None
    if key is not None and isinstance(value, dict):
        value = value.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _extract_class_pairs(
    context: SummaryEvaluatorContext,
    judge_name: str,
    class_config: "ClassConfig",
    score_key: Optional[str] = None,
    human_score_key: Optional[str] = None,
) -> list[tuple[bool, bool]]:
    """Extract paired (judge_pass, human_pass) boolean classifications.

    Applies ClassConfig thresholds to convert raw scores to pass/fail,
    then pairs judge and human classifications.

    :param context: Summary evaluator context.
    :param judge_name: Name of the judge evaluator.
    :param class_config: ClassConfig with thresholds for pass/fail bucketing.
    :param score_key: Key to extract judge score from result dicts.
    :param human_score_key: Key to extract human score from expected_output dicts.
    :return: List of (judge_passes, human_passes) boolean tuples.
    """
    judge_results = context.evaluation_results.get(judge_name, [])
    human_labels = context.expected_outputs

    pairs = []
    for judge_result, human_label in zip(judge_results, human_labels):
        judge_val = _resolve_value(judge_result, score_key)
        human_val = _resolve_value(human_label, human_score_key)
        if judge_val is None or human_val is None:
            continue

        judge_pass = class_config.classify(judge_val)
        human_pass = class_config.classify(human_val)
        if judge_pass is not None and human_pass is not None:
            pairs.append((judge_pass, human_pass))
    return pairs


def _resolve_value(value: Any, key: Optional[str] = None) -> Any:
    """Extract a value from a scalar or dict."""
    if value is None:
        return None
    if key is not None and isinstance(value, dict):
        return value.get(key)
    return value


class ClassConfig:
    """Pass/fail classification config applied post-hoc to judge and human scores.

    Reuses the threshold logic from ScoreStructuredOutput but applied after judging.
    Supports score, boolean, and categorical output types.

    For score type: values >= pass_threshold are classified as pass.
    For boolean type: values matching pass_when are classified as pass.
    For categorical type: values in pass_values are classified as pass.
    """

    def __init__(
        self,
        output_type: str = "score",
        pass_thresholds: Optional[list[float]] = None,
        pass_when: Optional[bool] = None,
        pass_values: Optional[list[str]] = None,
    ) -> None:
        """Initialize class config.

        :param output_type: One of "score", "boolean", "categorical".
        :param pass_thresholds: For score type, list of thresholds. Single value means >= threshold is pass.
        :param pass_when: For boolean type, which boolean value counts as pass.
        :param pass_values: For categorical type, which category values count as pass.
        :raises ValueError: If parameters are invalid for the given output_type.
        """
        if output_type not in ("score", "boolean", "categorical"):
            raise ValueError(f"output_type must be 'score', 'boolean', or 'categorical', got '{output_type}'")
        self.output_type = output_type
        self.pass_thresholds = pass_thresholds
        self.pass_when = pass_when
        self.pass_values = pass_values

        if output_type == "score" and not pass_thresholds:
            raise ValueError("pass_thresholds required for score output_type")
        if output_type == "boolean" and pass_when is None:
            raise ValueError("pass_when required for boolean output_type")
        if output_type == "categorical" and not pass_values:
            raise ValueError("pass_values required for categorical output_type")

    def classify(self, value: Any) -> Optional[bool]:
        """Classify a value as pass (True) or fail (False).

        :param value: The value to classify.
        :return: True if pass, False if fail, None if value cannot be classified.
        """
        if value is None:
            return None

        if self.output_type == "score":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return None
            threshold = self.pass_thresholds[0]  # type: ignore[index]
            return float(value) >= threshold

        if self.output_type == "boolean":
            if not isinstance(value, bool):
                return None
            return value == self.pass_when

        if self.output_type == "categorical":
            return str(value) in (self.pass_values or [])

        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API persistence."""
        d: dict[str, Any] = {"output_type": self.output_type}
        if self.pass_thresholds is not None:
            d["pass_thresholds"] = self.pass_thresholds
        if self.pass_when is not None:
            d["pass_when"] = self.pass_when
        if self.pass_values is not None:
            d["pass_values"] = self.pass_values
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClassConfig":
        """Deserialize from API response dict."""
        return cls(
            output_type=data["output_type"],
            pass_thresholds=data.get("pass_thresholds"),
            pass_when=data.get("pass_when"),
            pass_values=data.get("pass_values"),
        )


class AlignmentConfig:
    """Disagreement classification config for measuring judge-human alignment.

    Categorizes the disagreement between judge and human into classes
    (e.g., Good, OK, Bad) based on configurable thresholds.

    For score type: |judge - human| compared against thresholds.
    For boolean type: agreement (Good) vs disagreement (Bad).
    For categorical type: exact match (Good) vs mismatch (Bad).
    """

    def __init__(
        self,
        output_type: str = "score",
        alignment_thresholds: Optional[list[float]] = None,
        class_labels: Optional[list[str]] = None,
    ) -> None:
        """Initialize alignment config.

        :param output_type: One of "score", "boolean", "categorical".
        :param alignment_thresholds: For score type, sorted thresholds defining class boundaries.
            E.g., [10, 20] means: Good if delta < 10, OK if 10 <= delta < 20, Bad if delta >= 20.
        :param class_labels: Labels for each class. Length should be len(thresholds) + 1.
            Defaults to ["good", "ok", "bad"] for 2 thresholds.
        :raises ValueError: If parameters are invalid.
        """
        if output_type not in ("score", "boolean", "categorical"):
            raise ValueError(f"output_type must be 'score', 'boolean', or 'categorical', got '{output_type}'")
        self.output_type = output_type
        self.alignment_thresholds = alignment_thresholds or []

        if output_type == "score" and not alignment_thresholds:
            raise ValueError("alignment_thresholds required for score output_type")

        if class_labels is not None:
            self.class_labels = class_labels
        elif output_type == "score":
            n_classes = len(self.alignment_thresholds) + 1
            if n_classes == 2:
                self.class_labels = ["good", "bad"]
            elif n_classes == 3:
                self.class_labels = ["good", "ok", "bad"]
            else:
                self.class_labels = [f"class_{i}" for i in range(n_classes)]
        else:
            self.class_labels = ["good", "bad"]

    def classify_disagreement(self, judge_value: Any, human_value: Any) -> Optional[str]:
        """Classify the disagreement between judge and human values.

        :param judge_value: The judge's output value.
        :param human_value: The human's label value.
        :return: Class label string, or None if values cannot be compared.
        """
        if judge_value is None or human_value is None:
            return None

        if self.output_type == "score":
            if (
                not isinstance(judge_value, (int, float))
                or isinstance(judge_value, bool)
                or not isinstance(human_value, (int, float))
                or isinstance(human_value, bool)
            ):
                return None
            delta = abs(float(judge_value) - float(human_value))
            for i, threshold in enumerate(self.alignment_thresholds):
                if delta < threshold:
                    return self.class_labels[i]
            return self.class_labels[-1]

        if self.output_type == "boolean":
            if not isinstance(judge_value, bool) or not isinstance(human_value, bool):
                return None
            return self.class_labels[0] if judge_value == human_value else self.class_labels[-1]

        if self.output_type == "categorical":
            return self.class_labels[0] if str(judge_value) == str(human_value) else self.class_labels[-1]

        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API persistence."""
        return {
            "output_type": self.output_type,
            "alignment_thresholds": self.alignment_thresholds,
            "class_labels": self.class_labels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlignmentConfig":
        """Deserialize from API response dict."""
        return cls(
            output_type=data["output_type"],
            alignment_thresholds=data.get("alignment_thresholds"),
            class_labels=data.get("class_labels"),
        )


class MAEEvaluator(BaseSummaryEvaluator):
    """Mean Absolute Error between judge scores and human scores.

    Computes: (1/n) * sum(|judge_score - human_score|) for all scored records.
    """

    def __init__(
        self,
        judge_name: str,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        """Initialize MAE evaluator.

        :param judge_name: Name of the judge evaluator in evaluation_results.
        :param score_key: Key to extract score from judge result dicts (None if scalar).
        :param human_score_key: Key to extract score from expected_output dicts (None if scalar).
        """
        super().__init__(name="mae")
        self._judge_name = judge_name
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        pairs = _extract_score_pairs(context, self._judge_name, self._score_key, self._human_score_key)
        if not pairs:
            return {"value": None, "count": 0}
        errors = [abs(j - h) for j, h in pairs]
        return {"value": sum(errors) / len(errors), "count": len(pairs)}


class RMSEEvaluator(BaseSummaryEvaluator):
    """Root Mean Squared Error between judge scores and human scores.

    Computes: sqrt((1/n) * sum((judge_score - human_score)^2)).
    """

    def __init__(
        self,
        judge_name: str,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="rmse")
        self._judge_name = judge_name
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        pairs = _extract_score_pairs(context, self._judge_name, self._score_key, self._human_score_key)
        if not pairs:
            return {"value": None, "count": 0}
        mse = sum((j - h) ** 2 for j, h in pairs) / len(pairs)
        return {"value": math.sqrt(mse), "count": len(pairs)}


class BiasEvaluator(BaseSummaryEvaluator):
    """Mean signed difference (judge - human) measuring systematic over/under scoring.

    Positive bias means the judge systematically scores higher than humans.
    """

    def __init__(
        self,
        judge_name: str,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="bias")
        self._judge_name = judge_name
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        pairs = _extract_score_pairs(context, self._judge_name, self._score_key, self._human_score_key)
        if not pairs:
            return {"value": None, "count": 0}
        diffs = [j - h for j, h in pairs]
        return {"value": sum(diffs) / len(diffs), "count": len(pairs)}


class CorrelationEvaluator(BaseSummaryEvaluator):
    """Pearson correlation coefficient between judge scores and human scores.

    Returns a value between -1 and 1 where 1 means perfect positive correlation.
    """

    def __init__(
        self,
        judge_name: str,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="correlation")
        self._judge_name = judge_name
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        pairs = _extract_score_pairs(context, self._judge_name, self._score_key, self._human_score_key)
        if len(pairs) < 2:
            return {"value": None, "count": len(pairs)}

        judge_scores = [j for j, _ in pairs]
        human_scores = [h for _, h in pairs]
        n = len(pairs)

        mean_j = sum(judge_scores) / n
        mean_h = sum(human_scores) / n

        cov = sum((j - mean_j) * (h - mean_h) for j, h in pairs) / n
        std_j = math.sqrt(sum((j - mean_j) ** 2 for j in judge_scores) / n)
        std_h = math.sqrt(sum((h - mean_h) ** 2 for h in human_scores) / n)

        if std_j == 0 or std_h == 0:
            return {"value": None, "count": n}

        return {"value": cov / (std_j * std_h), "count": n}


class TPREvaluator(BaseSummaryEvaluator):
    """True Positive Rate (sensitivity/recall) of judge classifications.

    Measures: of all records that humans classified as pass, what fraction did the judge also classify as pass.
    Requires ClassConfig to convert raw scores to pass/fail.
    """

    def __init__(
        self,
        judge_name: str,
        class_config: ClassConfig,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="tpr")
        self._judge_name = judge_name
        self._class_config = class_config
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        pairs = _extract_class_pairs(
            context, self._judge_name, self._class_config, self._score_key, self._human_score_key
        )
        if not pairs:
            return {"value": None, "count": 0}

        true_positives = sum(1 for j_pass, h_pass in pairs if h_pass and j_pass)
        actual_positives = sum(1 for _, h_pass in pairs if h_pass)

        if actual_positives == 0:
            return {"value": None, "count": len(pairs)}

        return {"value": true_positives / actual_positives, "count": len(pairs)}


class TNREvaluator(BaseSummaryEvaluator):
    """True Negative Rate (specificity) of judge classifications.

    Measures: of all records that humans classified as fail, what fraction did the judge also classify as fail.
    Requires ClassConfig to convert raw scores to pass/fail.
    """

    def __init__(
        self,
        judge_name: str,
        class_config: ClassConfig,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="tnr")
        self._judge_name = judge_name
        self._class_config = class_config
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        pairs = _extract_class_pairs(
            context, self._judge_name, self._class_config, self._score_key, self._human_score_key
        )
        if not pairs:
            return {"value": None, "count": 0}

        true_negatives = sum(1 for j_pass, h_pass in pairs if not h_pass and not j_pass)
        actual_negatives = sum(1 for _, h_pass in pairs if not h_pass)

        if actual_negatives == 0:
            return {"value": None, "count": len(pairs)}

        return {"value": true_negatives / actual_negatives, "count": len(pairs)}


def compute_consistency(
    experiment_result: Any,
    judge_name: str = "judge",
    score_key: Optional[str] = None,
) -> dict[str, Any]:
    """Compute score consistency across multiple runs of the same scenario.

    This is a standalone function (not a BaseSummaryEvaluator) because the
    experiment framework runs summary evaluators independently per run, so a
    summary evaluator never sees cross-run data. This function operates on the
    full ``ExperimentResult`` which contains all runs.

    Measures the average standard deviation of judge scores across runs for
    each scenario. Lower values indicate more consistent judging.

    :param experiment_result: An ``ExperimentResult`` dict containing a ``runs`` key.
    :param judge_name: Name of the judge evaluator in evaluation results.
    :param score_key: Key to extract score from judge result dicts (None if scalar).
    :return: Dict with ``value`` (avg std dev), ``count`` (scenarios measured),
        and ``per_scenario`` (dict of record_id to std dev).
    """
    runs = experiment_result.get("runs", [])
    if len(runs) < 2:
        return {"value": 0.0, "count": 0, "per_scenario": {}}

    # Collect scores per scenario (record_id) across runs
    scores_by_scenario: dict[str, list[float]] = {}
    for run in runs:
        for row in run.rows:
            record_id = row.get("record_id", "")
            judge_eval = row.get("evaluations", {}).get(judge_name, {})
            raw_value = judge_eval.get("value") if isinstance(judge_eval, dict) else judge_eval
            score = _extract_numeric(raw_value, score_key)
            if score is not None:
                if record_id not in scores_by_scenario:
                    scores_by_scenario[record_id] = []
                scores_by_scenario[record_id].append(score)

    # Compute std dev per scenario
    per_scenario: dict[str, float] = {}
    for record_id, scores in scores_by_scenario.items():
        if len(scores) >= 2:
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
            per_scenario[record_id] = math.sqrt(variance)

    if not per_scenario:
        return {"value": 0.0, "count": 0, "per_scenario": {}}

    avg_stddev = sum(per_scenario.values()) / len(per_scenario)
    return {"value": avg_stddev, "count": len(per_scenario), "per_scenario": per_scenario}


class DisagreementEvaluator(BaseSummaryEvaluator):
    """Categorize records by judge-human disagreement level.

    Uses AlignmentConfig to classify each record's disagreement into classes
    (e.g., Good/OK/Bad) and returns counts and record indices per class.
    """

    def __init__(
        self,
        judge_name: str,
        alignment_config: AlignmentConfig,
        score_key: Optional[str] = None,
        human_score_key: Optional[str] = None,
    ) -> None:
        super().__init__(name="disagreement")
        self._judge_name = judge_name
        self._alignment_config = alignment_config
        self._score_key = score_key
        self._human_score_key = human_score_key

    def evaluate(self, context: SummaryEvaluatorContext) -> JSONType:
        judge_results = context.evaluation_results.get(self._judge_name, [])
        human_labels = context.expected_outputs

        classes: dict[str, list[int]] = {label: [] for label in self._alignment_config.class_labels}

        for idx, (judge_result, human_label) in enumerate(zip(judge_results, human_labels)):
            judge_val = _resolve_value(judge_result, self._score_key)
            human_val = _resolve_value(human_label, self._human_score_key)

            classification = self._alignment_config.classify_disagreement(judge_val, human_val)
            if classification is not None and classification in classes:
                classes[classification].append(idx)

        counts = {label: len(indices) for label, indices in classes.items()}
        return {"classes": classes, "counts": counts, "total": sum(counts.values())}
