"""Eval tuning framework for iteratively calibrating LLM judges.

Provides the EvalTuningProject orchestrator that manages the lifecycle of
judge calibration: initializing judge configs, running iterations on dataset
splits, computing agreement metrics, and optionally running prompt optimization.
"""

from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field
import random
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Literal
from typing import Optional
from typing import Sequence
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import StructuredOutput
from ddtrace.llmobs._evaluators.metrics import AlignmentConfig
from ddtrace.llmobs._evaluators.metrics import BiasEvaluator
from ddtrace.llmobs._evaluators.metrics import ClassConfig
from ddtrace.llmobs._evaluators.metrics import ConsistencyEvaluator
from ddtrace.llmobs._evaluators.metrics import CorrelationEvaluator
from ddtrace.llmobs._evaluators.metrics import DisagreementEvaluator
from ddtrace.llmobs._evaluators.metrics import MAEEvaluator
from ddtrace.llmobs._evaluators.metrics import RMSEEvaluator
from ddtrace.llmobs._evaluators.metrics import TNREvaluator
from ddtrace.llmobs._evaluators.metrics import TPREvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import EvaluatorType
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import SummaryEvaluatorType
from ddtrace.llmobs.types import JSONType


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs

log = get_logger(__name__)

_SPLIT_SEED = 42


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TrainingExample:
    """A dataset record used as a few-shot example in the judge prompt.

    :param id: Unique identifier for this training example.
    :param record_id: The dataset record ID this example references.
    :param input_data: Resolved input data from the dataset record.
    :param expected_output: Resolved expected output (human label) from the dataset record.
    """

    id: str
    record_id: str
    input_data: dict[str, Any]
    expected_output: Any


@dataclass
class PromptVersion:
    """A versioned prompt for the judge.

    :param id: Unique identifier.
    :param version: Auto-incremented version number.
    :param content: The prompt text.
    """

    id: str
    version: int
    content: str


@dataclass
class JudgeConfig:
    """Versioned judge configuration wrapping LLMJudge.

    Combines model, prompt (versioned), structured output, training examples,
    and run configuration into a single snapshot. Each mutation creates a new
    config version so that iterations always reference an immutable snapshot.

    :param id: Unique identifier for this config version.
    :param project_id: Parent eval tuning project ID.
    :param model: LLM model identifier (e.g. ``"gpt-4o"``).
    :param provider: LLM provider name.
    :param prompt: The current PromptVersion.
    :param structured_output: Output schema for the judge.
    :param training_examples: Few-shot examples to inject into the prompt.
    :param num_runs: Number of times to run the judge per scenario for consistency.
    :param system_prompt: Optional system prompt for the judge.
    :param model_params: Additional model parameters (temperature, etc.).
    :param client_options: Provider-specific client options.
    """

    id: str
    project_id: str
    model: str
    provider: str
    prompt: PromptVersion
    structured_output: StructuredOutput
    training_examples: list[TrainingExample] = field(default_factory=list)
    num_runs: int = 1
    system_prompt: Optional[str] = None
    model_params: Optional[dict[str, Any]] = None
    client_options: Optional[dict[str, Any]] = None

    def to_llm_judge(self, name: str = "judge") -> LLMJudge:
        """Construct an LLMJudge with training examples injected into the prompt.

        :param name: Evaluator name for identification in results.
        :return: Configured LLMJudge instance.
        """
        prompt_with_examples = self._build_prompt_with_examples()
        return LLMJudge(
            user_prompt=prompt_with_examples,
            system_prompt=self.system_prompt,
            structured_output=self.structured_output,
            provider=self.provider,  # type: ignore[arg-type]
            model=self.model,
            model_params=self.model_params,
            name=name,
            client_options=self.client_options,
        )

    def _build_prompt_with_examples(self) -> str:
        """Build the user prompt with training examples appended.

        Training examples are formatted as few-shot examples at the end of
        the prompt template to guide the judge's scoring behavior.

        :return: Prompt string with training examples injected.
        """
        prompt = self.prompt.content
        if not self.training_examples:
            return prompt

        examples_parts = ["\n\n## Training Examples\nUse these examples to calibrate your scoring:\n"]
        for i, example in enumerate(self.training_examples, 1):
            examples_parts.append(f"### Example {i}")
            examples_parts.append(f"Input: {example.input_data}")
            if example.expected_output is not None:
                examples_parts.append(f"Expected Score/Label: {example.expected_output}")
            examples_parts.append("")

        return prompt + "\n".join(examples_parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API persistence."""
        so_config: dict[str, Any] = {}
        so_type = "custom"
        if isinstance(self.structured_output, ScoreStructuredOutput):
            so_type = "score"
            so_config = {
                "description": self.structured_output.description,
                "min_score": self.structured_output.min_score,
                "max_score": self.structured_output.max_score,
                "reasoning": self.structured_output.reasoning,
                "min_threshold": self.structured_output.min_threshold,
                "max_threshold": self.structured_output.max_threshold,
            }
        elif isinstance(self.structured_output, BooleanStructuredOutput):
            so_type = "boolean"
            so_config = {
                "description": self.structured_output.description,
                "reasoning": self.structured_output.reasoning,
                "pass_when": self.structured_output.pass_when,
            }
        elif isinstance(self.structured_output, CategoricalStructuredOutput):
            so_type = "categorical"
            so_config = {
                "categories": self.structured_output.categories,
                "reasoning": self.structured_output.reasoning,
                "pass_values": self.structured_output.pass_values,
            }
        elif isinstance(self.structured_output, dict):
            so_type = "custom"
            so_config = self.structured_output

        return {
            "model": self.model,
            "provider": self.provider,
            "prompt_content": self.prompt.content,
            "prompt_version": self.prompt.version,
            "structured_output_type": so_type,
            "structured_output_config": so_config,
            "training_example_ids": [ex.id for ex in self.training_examples],
            "num_runs": self.num_runs,
            "system_prompt": self.system_prompt,
            "model_params": self.model_params,
            "client_options": self.client_options,
        }

    @classmethod
    def from_api_response(cls, data: dict[str, Any], training_examples: list[TrainingExample]) -> "JudgeConfig":
        """Deserialize from API response.

        :param data: API response attributes dict.
        :param training_examples: Resolved training examples.
        :return: JudgeConfig instance.
        """
        so_type = data.get("structured_output_type", "score")
        so_config = data.get("structured_output_config", {})

        structured_output: StructuredOutput
        if so_type == "score":
            structured_output = ScoreStructuredOutput(**so_config)
        elif so_type == "boolean":
            structured_output = BooleanStructuredOutput(**so_config)
        elif so_type == "categorical":
            structured_output = CategoricalStructuredOutput(**so_config)
        else:
            structured_output = so_config

        return cls(
            id=data["id"],
            project_id=data["project_id"],
            model=data["model"],
            provider=data["provider"],
            prompt=PromptVersion(
                id=data.get("prompt_id", ""),
                version=data.get("prompt_version", 1),
                content=data.get("prompt_content", ""),
            ),
            structured_output=structured_output,
            training_examples=training_examples,
            num_runs=data.get("num_runs", 1),
            system_prompt=data.get("system_prompt"),
            model_params=data.get("model_params"),
            client_options=data.get("client_options"),
        )


@dataclass
class SplitMetadata:
    """Persisted dataset split assignments.

    :param id: Unique identifier.
    :param project_id: Parent project ID.
    :param train_ratio: Fraction of records in train split.
    :param dev_ratio: Fraction of records in dev split.
    :param test_ratio: Fraction of records in test split.
    :param group_column: Optional metadata key for stratified splitting.
    :param train_record_ids: Record IDs assigned to train split.
    :param dev_record_ids: Record IDs assigned to dev split.
    :param test_record_ids: Record IDs assigned to test split.
    """

    id: str
    project_id: str
    train_ratio: float
    dev_ratio: float
    test_ratio: float
    group_column: Optional[str]
    train_record_ids: list[str]
    dev_record_ids: list[str]
    test_record_ids: list[str]


@dataclass
class IterationResult:
    """Results from a single eval tuning iteration.

    :param id: Iteration ID.
    :param judge_config_id: The judge config snapshot used.
    :param status: Current status (CREATED, RUNNING, COMPLETED, FAILED).
    :param experiment_results: Mapping of split name to ExperimentResult.
    :param experiment_urls: Mapping of split name to experiment URL.
    :param metrics: Computed metrics (populated after calc_metrics).
    """

    id: str
    judge_config_id: str
    status: str
    experiment_results: dict[str, ExperimentResult] = field(default_factory=dict)
    experiment_urls: dict[str, str] = field(default_factory=dict)
    metrics: Optional[dict[str, Any]] = None


@dataclass
class ComparisonResult:
    """Results from comparing two iterations.

    :param iteration_id_1: First iteration ID.
    :param iteration_id_2: Second iteration ID.
    :param per_scenario_deltas: Per-scenario score differences.
    :param pct_improved: Percentage of scenarios that improved.
    :param pct_worsened: Percentage of scenarios that worsened.
    :param pct_unchanged: Percentage of scenarios unchanged.
    :param metrics_delta: Difference in aggregate metrics between iterations.
    """

    iteration_id_1: str
    iteration_id_2: str
    per_scenario_deltas: dict[str, float]
    pct_improved: float
    pct_worsened: float
    pct_unchanged: float
    metrics_delta: dict[str, Optional[float]]


# ---------------------------------------------------------------------------
# Dataset splitting with group column support
# ---------------------------------------------------------------------------


def split_dataset(
    dataset: Dataset,
    train_ratio: float,
    dev_ratio: float,
    test_ratio: float,
    group_column: Optional[str] = None,
    seed: int = _SPLIT_SEED,
) -> tuple[list[DatasetRecord], list[DatasetRecord], list[DatasetRecord]]:
    """Split dataset records into train/dev/test with optional group-aware stratification.

    When ``group_column`` is provided, all records sharing the same group value
    are kept together in the same split. Groups are assigned to splits
    proportionally to the requested ratios.

    :param dataset: The dataset to split.
    :param train_ratio: Fraction for train split.
    :param dev_ratio: Fraction for dev split.
    :param test_ratio: Fraction for test split.
    :param group_column: Optional metadata key for stratified splitting.
    :param seed: Random seed for reproducibility.
    :return: Tuple of (train_records, dev_records, test_records).
    :raises ValueError: If ratios don't sum to ~1.0 or any split would be empty.
    """
    total = train_ratio + dev_ratio + test_ratio
    if not (0.99 <= total <= 1.01):
        raise ValueError(f"Split ratios must sum to 1.0, got {total:.4f}")

    records = list(dataset)
    rng = random.Random(seed)  # nosec B311

    if group_column is not None:
        return _split_by_group(records, train_ratio, dev_ratio, group_column, rng)

    rng.shuffle(records)
    n = len(records)
    train_end = int(train_ratio * n)
    dev_end = int((train_ratio + dev_ratio) * n)

    train = records[:train_end]
    dev = records[train_end:dev_end]
    test = records[dev_end:]

    _validate_splits(train, dev, test, len(records))
    return train, dev, test


def _split_by_group(
    records: list[DatasetRecord],
    train_ratio: float,
    dev_ratio: float,
    group_column: str,
    rng: random.Random,
) -> tuple[list[DatasetRecord], list[DatasetRecord], list[DatasetRecord]]:
    """Split records ensuring all records in the same group stay together.

    Groups are sorted by size (descending) then assigned greedily to the split
    that is furthest below its target count.

    :param records: All dataset records.
    :param train_ratio: Target fraction for train.
    :param dev_ratio: Target fraction for dev.
    :param group_column: Metadata key identifying the group.
    :param rng: Random instance for shuffling ties.
    :return: Tuple of (train_records, dev_records, test_records).
    """
    # Group records by the group_column value in metadata
    groups: dict[str, list[DatasetRecord]] = {}
    for record in records:
        metadata = record.get("metadata", {}) or {}
        group_key = str(metadata.get(group_column, "__ungrouped__"))
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(record)

    # Shuffle group keys for randomness among same-size groups, then sort by size descending
    group_keys = list(groups.keys())
    rng.shuffle(group_keys)
    group_keys.sort(key=lambda k: len(groups[k]), reverse=True)

    n = len(records)
    targets = {
        "train": train_ratio * n,
        "dev": dev_ratio * n,
        "test": (1.0 - train_ratio - dev_ratio) * n,
    }
    counts: dict[str, int] = {"train": 0, "dev": 0, "test": 0}
    split_records: dict[str, list[DatasetRecord]] = {"train": [], "dev": [], "test": []}

    # Greedy assignment: assign each group to the split that needs the most records
    for key in group_keys:
        group = groups[key]
        # Pick the split with the largest remaining deficit
        best_split = max(targets.keys(), key=lambda s: targets[s] - counts[s])
        split_records[best_split].extend(group)
        counts[best_split] += len(group)

    _validate_splits(split_records["train"], split_records["dev"], split_records["test"], n)
    return split_records["train"], split_records["dev"], split_records["test"]


def _validate_splits(
    train: list[DatasetRecord],
    dev: list[DatasetRecord],
    test: list[DatasetRecord],
    total: int,
) -> None:
    """Raise ValueError if any split is empty."""
    for name, split in [("train", train), ("dev", dev), ("test", test)]:
        if not split:
            raise ValueError(
                f"Dataset split '{name}' is empty. "
                f"Dataset has {total} records, which is too few for the requested split ratios."
            )


def _make_sub_dataset(dataset: Dataset, split_name: str, records: list[DatasetRecord]) -> Dataset:
    """Create a sub-dataset from a list of records.

    :param dataset: The parent dataset.
    :param split_name: Name suffix (e.g. "train", "dev", "test").
    :param records: Records to include.
    :return: New Dataset with the given records.
    """
    return Dataset(
        name=f"[{split_name}] {dataset.name}",
        project=dataset.project,
        dataset_id=dataset._id,
        records=[deepcopy(r) for r in records],
        description=dataset.description,
        latest_version=dataset._latest_version,
        version=dataset._version,
        _dne_client=dataset._dne_client,
    )


# ---------------------------------------------------------------------------
# EvalTuningProject orchestrator
# ---------------------------------------------------------------------------


class EvalTuningProject:
    """Orchestrator for the eval tuning workflow.

    Manages the full lifecycle of LLM judge calibration: configuring judges,
    splitting datasets, running iterations, computing metrics, comparing
    iterations, and optionally running prompt optimization.

    Example usage::

        project = LLMObs.eval_tuning_project(
            name="my_judge_tuning",
            dataset=dataset,
            project_name="my_project",
        )

        # Configure judge
        judge_config = project.init_judge(
            model="gpt-4o",
            provider="openai",
            prompt="Rate the quality of this response: {{output_data}}",
            structured_output=ScoreStructuredOutput(
                description="Quality score", min_score=0, max_score=100, reasoning=True,
            ),
        )

        # Configure scoring classes and alignment
        project.set_class_config(ClassConfig(output_type="score", pass_thresholds=[50]))
        project.set_alignment_config(AlignmentConfig(output_type="score", alignment_thresholds=[10, 20]))

        # Split dataset
        project.split_dataset(train_ratio=0.3, dev_ratio=0.3, test_ratio=0.4)

        # Run iterations
        iteration = project.run_iteration(splits=["train", "dev"])
        metrics = project.calc_metrics(iteration.id)

        # Tune judge and iterate
        project.update_judge(prompt="Improved prompt...")
        iteration2 = project.run_iteration(splits=["train", "dev"])
        comparison = project.compare_iterations(iteration.id, iteration2.id)
    """

    def __init__(
        self,
        name: str,
        dataset: Dataset,
        project_name: str,
        _llmobs_instance: Optional["LLMObs"] = None,
        description: str = "",
        project_id: Optional[str] = None,
    ) -> None:
        """Initialize an eval tuning project.

        :param name: Name of the eval tuning project.
        :param dataset: The dataset containing scenarios with human labels.
        :param project_name: LLMObs project name for organizing experiments.
        :param _llmobs_instance: Internal LLMObs instance.
        :param description: Optional project description.
        :param project_id: If resuming, the existing project ID.
        """
        self.name = name
        self._dataset = dataset
        self._project_name = project_name
        self._llmobs_instance = _llmobs_instance
        self._description = description
        self._id = project_id

        # Current configs (set via init/update methods)
        self._judge_config: Optional[JudgeConfig] = None
        self._class_config: Optional[ClassConfig] = None
        self._alignment_config: Optional[AlignmentConfig] = None
        self._split_metadata: Optional[SplitMetadata] = None

        # Split datasets (populated by split_dataset())
        self._train_ds: Optional[Dataset] = None
        self._dev_ds: Optional[Dataset] = None
        self._test_ds: Optional[Dataset] = None

        # Iteration tracking
        self._iterations: list[IterationResult] = []

        # Prompt version counter
        self._prompt_version = 0

        # Persist project via API
        if self._id is None:
            self._create_project()

    def _create_project(self) -> None:
        """Create the eval tuning project via the API."""
        if not self._llmobs_instance:
            return
        resp = self._llmobs_instance._dne_client.eval_tuning_project_create(
            name=self.name,
            dataset_id=self._dataset._id,
            description=self._description,
        )
        self._id = resp["id"]

    # ------------------------------------------------------------------
    # Judge config management
    # ------------------------------------------------------------------

    def init_judge(
        self,
        model: str,
        provider: str,
        prompt: str,
        structured_output: StructuredOutput,
        num_runs: int = 1,
        system_prompt: Optional[str] = None,
        model_params: Optional[dict[str, Any]] = None,
        client_options: Optional[dict[str, Any]] = None,
    ) -> JudgeConfig:
        """Initialize the judge configuration.

        Creates the first judge config version for this project. Subsequent
        changes should use :meth:`update_judge`.

        :param model: LLM model identifier (e.g. ``"gpt-4o"``).
        :param provider: LLM provider (``"openai"``, ``"anthropic"``, etc.).
        :param prompt: User prompt template with ``{{field}}`` variable syntax.
        :param structured_output: Output schema (ScoreStructuredOutput, BooleanStructuredOutput, etc.).
        :param num_runs: Number of judge runs per scenario for consistency measurement.
        :param system_prompt: Optional system prompt.
        :param model_params: Optional model parameters (temperature, etc.).
        :param client_options: Optional provider-specific client config.
        :return: The created JudgeConfig.
        """
        self._prompt_version = 1
        prompt_version = PromptVersion(id="", version=1, content=prompt)

        config = JudgeConfig(
            id="",
            project_id=self._id or "",
            model=model,
            provider=provider,
            prompt=prompt_version,
            structured_output=structured_output,
            num_runs=num_runs,
            system_prompt=system_prompt,
            model_params=model_params,
            client_options=client_options,
        )

        # Persist via API
        if self._llmobs_instance and self._id:
            resp = self._llmobs_instance._dne_client.eval_tuning_judge_config_create(
                project_id=self._id,
                config_data=config.to_dict(),
            )
            config.id = resp["id"]
            config.prompt.id = resp.get("prompt_id", "")

        self._judge_config = config
        log.info("Initialized judge config: model=%s, provider=%s", model, provider)
        return config

    def update_judge(
        self,
        prompt: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        model_params: Optional[dict[str, Any]] = None,
        num_runs: Optional[int] = None,
    ) -> JudgeConfig:
        """Update the judge configuration, creating a new versioned snapshot.

        Any parameter not provided retains its current value. The prompt version
        is auto-incremented if the prompt content changes.

        :param prompt: New user prompt template (auto-increments version).
        :param model: New model identifier.
        :param system_prompt: New system prompt.
        :param model_params: New model parameters.
        :param num_runs: New number of runs per scenario.
        :return: The new JudgeConfig version.
        :raises ValueError: If no judge config has been initialized.
        """
        if self._judge_config is None:
            raise ValueError("No judge config initialized. Call init_judge() first.")

        current = self._judge_config

        # Auto-increment prompt version if content changed
        new_prompt_version = current.prompt
        if prompt is not None and prompt != current.prompt.content:
            self._prompt_version += 1
            new_prompt_version = PromptVersion(id="", version=self._prompt_version, content=prompt)

        new_config = JudgeConfig(
            id="",
            project_id=self._id or "",
            model=model or current.model,
            provider=current.provider,
            prompt=new_prompt_version,
            structured_output=current.structured_output,
            training_examples=list(current.training_examples),
            num_runs=num_runs if num_runs is not None else current.num_runs,
            system_prompt=system_prompt if system_prompt is not None else current.system_prompt,
            model_params=model_params if model_params is not None else current.model_params,
            client_options=current.client_options,
        )

        # Persist via API
        if self._llmobs_instance and self._id:
            resp = self._llmobs_instance._dne_client.eval_tuning_judge_config_create(
                project_id=self._id,
                config_data=new_config.to_dict(),
            )
            new_config.id = resp["id"]
            new_config.prompt.id = resp.get("prompt_id", "")

        self._judge_config = new_config
        log.info("Updated judge config: version=%d", new_config.prompt.version)
        return new_config

    # ------------------------------------------------------------------
    # Training example management
    # ------------------------------------------------------------------

    def add_training_example(self, record_index: int) -> TrainingExample:
        """Add a dataset record as a training example for the judge.

        The record's input and expected output are resolved from the dataset
        and will be injected as few-shot examples in the judge prompt.

        :param record_index: Index of the record in the dataset.
        :return: The created TrainingExample.
        :raises ValueError: If no judge config is initialized.
        :raises IndexError: If record_index is out of range.
        """
        if self._judge_config is None:
            raise ValueError("No judge config initialized. Call init_judge() first.")

        record = self._dataset[record_index]
        example = TrainingExample(
            id="",
            record_id=record["record_id"],
            input_data=record["input_data"],
            expected_output=record.get("expected_output"),
        )

        # Persist via API
        if self._llmobs_instance and self._id:
            resp = self._llmobs_instance._dne_client.eval_tuning_training_example_create(
                project_id=self._id,
                record_id=record["record_id"],
            )
            example.id = resp["id"]

        self._judge_config.training_examples.append(example)
        log.info("Added training example: record_id=%s", record["record_id"])
        return example

    def remove_training_example(self, example_id: str) -> None:
        """Remove a training example from the judge config.

        :param example_id: ID of the training example to remove.
        :raises ValueError: If no judge config is initialized or example not found.
        """
        if self._judge_config is None:
            raise ValueError("No judge config initialized. Call init_judge() first.")

        original_len = len(self._judge_config.training_examples)
        self._judge_config.training_examples = [
            ex for ex in self._judge_config.training_examples if ex.id != example_id
        ]

        if len(self._judge_config.training_examples) == original_len:
            raise ValueError(f"Training example {example_id} not found.")

        # Persist via API
        if self._llmobs_instance and self._id:
            self._llmobs_instance._dne_client.eval_tuning_training_example_delete(
                project_id=self._id,
                example_id=example_id,
            )

        log.info("Removed training example: %s", example_id)

    def get_training_examples(self) -> list[TrainingExample]:
        """List current training examples.

        :return: List of training examples in the current judge config.
        """
        if self._judge_config is None:
            return []
        return list(self._judge_config.training_examples)

    # ------------------------------------------------------------------
    # Class and alignment config
    # ------------------------------------------------------------------

    def set_class_config(self, class_config: ClassConfig) -> ClassConfig:
        """Set the pass/fail classification config for metrics computation.

        :param class_config: ClassConfig defining pass/fail thresholds.
        :return: The set ClassConfig.
        """
        self._class_config = class_config

        # Persist via API
        if self._llmobs_instance and self._id:
            self._llmobs_instance._dne_client.eval_tuning_class_config_create(
                project_id=self._id,
                config_data=class_config.to_dict(),
            )

        log.info("Set class config: output_type=%s", class_config.output_type)
        return class_config

    def set_alignment_config(self, alignment_config: AlignmentConfig) -> AlignmentConfig:
        """Set the disagreement classification config for alignment measurement.

        :param alignment_config: AlignmentConfig defining disagreement thresholds.
        :return: The set AlignmentConfig.
        """
        self._alignment_config = alignment_config

        # Persist via API
        if self._llmobs_instance and self._id:
            self._llmobs_instance._dne_client.eval_tuning_alignment_config_create(
                project_id=self._id,
                config_data=alignment_config.to_dict(),
            )

        log.info("Set alignment config: output_type=%s", alignment_config.output_type)
        return alignment_config

    # ------------------------------------------------------------------
    # Dataset splitting
    # ------------------------------------------------------------------

    def split_dataset(
        self,
        train_ratio: float = 0.3,
        dev_ratio: float = 0.3,
        test_ratio: float = 0.4,
        group_column: Optional[str] = None,
    ) -> SplitMetadata:
        """Split the dataset into train/dev/test sets.

        When ``group_column`` is provided, records sharing the same group value
        (looked up in record metadata) are kept in the same split.

        :param train_ratio: Fraction for train split. Default 0.3.
        :param dev_ratio: Fraction for dev split. Default 0.3.
        :param test_ratio: Fraction for test split. Default 0.4.
        :param group_column: Optional metadata key for stratified splitting.
        :return: SplitMetadata with record ID assignments.
        :raises ValueError: If ratios are invalid or splits would be empty.
        """
        train_records, dev_records, test_records = split_dataset(
            self._dataset, train_ratio, dev_ratio, test_ratio, group_column
        )

        # Build sub-datasets
        self._train_ds = _make_sub_dataset(self._dataset, "train", train_records)
        self._dev_ds = _make_sub_dataset(self._dataset, "dev", dev_records)
        self._test_ds = _make_sub_dataset(self._dataset, "test", test_records)

        metadata = SplitMetadata(
            id="",
            project_id=self._id or "",
            train_ratio=train_ratio,
            dev_ratio=dev_ratio,
            test_ratio=test_ratio,
            group_column=group_column,
            train_record_ids=[r["record_id"] for r in train_records],
            dev_record_ids=[r["record_id"] for r in dev_records],
            test_record_ids=[r["record_id"] for r in test_records],
        )

        # Persist via API
        if self._llmobs_instance and self._id:
            resp = self._llmobs_instance._dne_client.eval_tuning_split_create(
                project_id=self._id,
                split_data={
                    "train_ratio": train_ratio,
                    "dev_ratio": dev_ratio,
                    "test_ratio": test_ratio,
                    "group_column": group_column,
                    "train_record_ids": metadata.train_record_ids,
                    "dev_record_ids": metadata.dev_record_ids,
                    "test_record_ids": metadata.test_record_ids,
                },
            )
            metadata.id = resp["id"]

        self._split_metadata = metadata
        log.info(
            "Dataset split: %d train, %d dev, %d test records",
            len(train_records),
            len(dev_records),
            len(test_records),
        )
        return metadata

    # ------------------------------------------------------------------
    # Iteration execution
    # ------------------------------------------------------------------

    def _get_split_dataset(self, split: str) -> Dataset:
        """Get the sub-dataset for a given split name.

        :param split: One of "train", "dev", "test".
        :return: The sub-dataset.
        :raises ValueError: If dataset has not been split or split name is invalid.
        """
        if self._split_metadata is None:
            raise ValueError("Dataset has not been split. Call split_dataset() first.")
        ds_map = {"train": self._train_ds, "dev": self._dev_ds, "test": self._test_ds}
        ds = ds_map.get(split)
        if ds is None:
            raise ValueError(f"Invalid split '{split}'. Must be 'train', 'dev', or 'test'.")
        return ds

    def _build_summary_evaluators(self, include_metrics: bool = True) -> list[SummaryEvaluatorType]:
        """Build the list of summary evaluators based on current configs.

        :param include_metrics: If False, skip metrics that require human labels (for unlabeled runs).
        :return: List of summary evaluators.
        """
        if not include_metrics:
            return []

        evaluators: list[SummaryEvaluatorType] = []
        judge_name = "judge"

        # Always include MAE, RMSE, Bias, Correlation for score-type judges
        if self._judge_config and isinstance(self._judge_config.structured_output, ScoreStructuredOutput):
            evaluators.append(MAEEvaluator(judge_name=judge_name))
            evaluators.append(RMSEEvaluator(judge_name=judge_name))
            evaluators.append(BiasEvaluator(judge_name=judge_name))
            evaluators.append(CorrelationEvaluator(judge_name=judge_name))

        # Add consistency evaluator if num_runs > 1
        if self._judge_config and self._judge_config.num_runs > 1:
            evaluators.append(ConsistencyEvaluator(judge_name=judge_name))

        # Add TPR/TNR if class config is set
        if self._class_config is not None:
            evaluators.append(TPREvaluator(judge_name=judge_name, class_config=self._class_config))
            evaluators.append(TNREvaluator(judge_name=judge_name, class_config=self._class_config))

        # Add disagreement evaluator if alignment config is set
        if self._alignment_config is not None:
            evaluators.append(
                DisagreementEvaluator(judge_name=judge_name, alignment_config=self._alignment_config)
            )

        return evaluators

    def run_iteration(
        self,
        splits: Optional[list[str]] = None,
        jobs: int = 10,
    ) -> IterationResult:
        """Run a judge iteration on the specified dataset splits.

        Creates experiments using the current judge config and runs them on
        the requested splits. Each iteration is tied to a specific judge config
        snapshot.

        :param splits: List of splits to run on (e.g. ``["train", "dev"]``). Defaults to ``["train", "dev"]``.
        :param jobs: Number of parallel jobs for experiment execution.
        :return: IterationResult with experiment results per split.
        :raises ValueError: If judge config or dataset split is not configured.
        """
        if self._judge_config is None:
            raise ValueError("No judge config initialized. Call init_judge() first.")
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError("LLMObs is not enabled.")

        splits = splits or ["train", "dev"]
        judge = self._judge_config.to_llm_judge(name="judge")
        summary_evaluators = self._build_summary_evaluators(include_metrics=True)

        iteration_result = IterationResult(
            id="",
            judge_config_id=self._judge_config.id,
            status="RUNNING",
        )

        # Persist iteration via API
        if self._id:
            resp = self._llmobs_instance._dne_client.eval_tuning_iteration_create(
                project_id=self._id,
                judge_config_id=self._judge_config.id,
            )
            iteration_result.id = resp["id"]

        try:
            for split_name in splits:
                ds = self._get_split_dataset(split_name)
                iter_num = len(self._iterations)
                exp_name = f"{self.name}_iter{iter_num}_{split_name}"

                experiment = self._llmobs_instance.experiment(
                    name=exp_name,
                    project_name=self._project_name,
                    dataset=ds,
                    task=self._make_passthrough_task(),
                    evaluators=[judge],
                    summary_evaluators=summary_evaluators,
                    config={"prompt": self._judge_config.prompt.content},
                    runs=self._judge_config.num_runs,
                )

                results = experiment.run(jobs=jobs, raise_errors=True)
                iteration_result.experiment_results[split_name] = results
                iteration_result.experiment_urls[split_name] = experiment.url

                # Persist history records
                self._persist_history(iteration_result.id, split_name, ds, results)

            iteration_result.status = "COMPLETED"
        except Exception:
            iteration_result.status = "FAILED"
            raise
        finally:
            # Update iteration status via API
            if self._id and iteration_result.id:
                self._llmobs_instance._dne_client.eval_tuning_iteration_update(
                    project_id=self._id,
                    iteration_id=iteration_result.id,
                    status=iteration_result.status,
                )

        self._iterations.append(iteration_result)
        log.info("Iteration completed: %s (splits: %s)", iteration_result.id, splits)
        return iteration_result

    def run_unlabeled_iteration(
        self,
        dataset: Dataset,
        jobs: int = 10,
    ) -> IterationResult:
        """Run the judge on unlabeled (production) data without computing metrics.

        Only records judge outputs; skips all metrics that require human labels.

        :param dataset: Dataset of unlabeled production data.
        :param jobs: Number of parallel jobs.
        :return: IterationResult with judge outputs but no metrics.
        :raises ValueError: If judge config is not initialized.
        """
        if self._judge_config is None:
            raise ValueError("No judge config initialized. Call init_judge() first.")
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError("LLMObs is not enabled.")

        judge = self._judge_config.to_llm_judge(name="judge")

        iteration_result = IterationResult(
            id="",
            judge_config_id=self._judge_config.id,
            status="RUNNING",
        )

        # Persist iteration via API
        if self._id:
            resp = self._llmobs_instance._dne_client.eval_tuning_iteration_create(
                project_id=self._id,
                judge_config_id=self._judge_config.id,
            )
            iteration_result.id = resp["id"]

        try:
            iter_num = len(self._iterations)
            exp_name = f"{self.name}_iter{iter_num}_unlabeled"

            # No summary evaluators for unlabeled data
            experiment = self._llmobs_instance.experiment(
                name=exp_name,
                project_name=self._project_name,
                dataset=dataset,
                task=self._make_passthrough_task(),
                evaluators=[judge],
                summary_evaluators=[],
                config={"prompt": self._judge_config.prompt.content},
                runs=self._judge_config.num_runs,
            )

            results = experiment.run(jobs=jobs, raise_errors=True)
            iteration_result.experiment_results["unlabeled"] = results
            iteration_result.experiment_urls["unlabeled"] = experiment.url

            # Persist history records
            self._persist_history(iteration_result.id, "unlabeled", dataset, results)

            iteration_result.status = "COMPLETED"
        except Exception:
            iteration_result.status = "FAILED"
            raise
        finally:
            if self._id and iteration_result.id:
                self._llmobs_instance._dne_client.eval_tuning_iteration_update(
                    project_id=self._id,
                    iteration_id=iteration_result.id,
                    status=iteration_result.status,
                )

        self._iterations.append(iteration_result)
        log.info("Unlabeled iteration completed: %s", iteration_result.id)
        return iteration_result

    @staticmethod
    def _make_passthrough_task() -> Callable:
        """Create a passthrough task that returns the input unchanged.

        In eval tuning, the "task" is the judge itself (an evaluator). The task
        function simply passes through the input data so the judge evaluator
        can score it.

        :return: A task function compatible with the experiment framework.
        """

        def passthrough_task(input_data: dict, config: Optional[dict] = None) -> dict:
            return input_data

        return passthrough_task

    def _persist_history(
        self,
        iteration_id: str,
        split_name: str,
        dataset: Dataset,
        results: ExperimentResult,
    ) -> None:
        """Persist per-record judge outputs as history records.

        :param iteration_id: The iteration these results belong to.
        :param split_name: The split name (train/dev/test/unlabeled).
        :param dataset: The dataset used.
        :param results: Experiment results containing per-row judge outputs.
        """
        if not self._llmobs_instance or not self._id or not iteration_id:
            return

        history_records = []
        for row in results.get("rows", []):
            record_id = row.get("record_id", "")
            judge_outputs = row.get("evaluations", {}).get("judge", {})
            expected_output = row.get("expected_output")

            history_records.append({
                "iteration_id": iteration_id,
                "scenario_id": record_id,
                "judge_outputs": judge_outputs,
                "type": split_name,
                "label": expected_output,
            })

        if history_records:
            self._llmobs_instance._dne_client.eval_tuning_history_create(
                project_id=self._id,
                records=history_records,
            )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def calc_metrics(self, iteration_id: str) -> dict[str, Any]:
        """Calculate agreement metrics for a completed iteration.

        Extracts metrics from the iteration's experiment summary evaluations
        and persists them as a MetricsRecord.

        :param iteration_id: ID of the iteration to calculate metrics for.
        :return: Dict of metric name to value.
        :raises ValueError: If iteration not found or not completed.
        """
        iteration = self._find_iteration(iteration_id)
        if iteration.status != "COMPLETED":
            raise ValueError(f"Iteration {iteration_id} is not completed (status: {iteration.status}).")

        all_metrics: dict[str, Any] = {}

        for split_name, results in iteration.experiment_results.items():
            summary_evals = results.get("summary_evaluations", {})
            split_metrics = {}
            for eval_name, eval_data in summary_evals.items():
                if isinstance(eval_data, dict) and "value" in eval_data:
                    split_metrics[eval_name] = eval_data["value"]
                else:
                    split_metrics[eval_name] = eval_data
            all_metrics[split_name] = split_metrics

        # Persist via API
        if self._llmobs_instance and self._id:
            self._llmobs_instance._dne_client.eval_tuning_metrics_create(
                project_id=self._id,
                iteration_id=iteration_id,
                values=all_metrics,
                class_config_id=getattr(self._class_config, "id", None) if hasattr(self._class_config, "id") else None,
                alignment_config_id=(
                    getattr(self._alignment_config, "id", None) if hasattr(self._alignment_config, "id") else None
                ),
            )

        iteration.metrics = all_metrics
        log.info("Metrics calculated for iteration %s: %s", iteration_id, all_metrics)
        return all_metrics

    # ------------------------------------------------------------------
    # Iteration management and comparison
    # ------------------------------------------------------------------

    def get_iterations(self) -> list[IterationResult]:
        """List all iterations for this project.

        :return: List of IterationResult objects.
        """
        return list(self._iterations)

    def compare_iterations(self, iteration_id_1: str, iteration_id_2: str) -> ComparisonResult:
        """Compare two iterations by per-scenario score deltas.

        Compares judge outputs for each scenario present in both iterations
        and computes improvement/regression statistics.

        :param iteration_id_1: ID of the first (baseline) iteration.
        :param iteration_id_2: ID of the second (candidate) iteration.
        :return: ComparisonResult with per-scenario deltas and aggregate stats.
        :raises ValueError: If either iteration is not found.
        """
        iter1 = self._find_iteration(iteration_id_1)
        iter2 = self._find_iteration(iteration_id_2)

        # Extract per-scenario scores from dev results (prefer dev for comparison)
        scores1 = self._extract_scenario_scores(iter1)
        scores2 = self._extract_scenario_scores(iter2)

        # Compute per-scenario deltas
        common_scenarios = set(scores1.keys()) & set(scores2.keys())
        deltas: dict[str, float] = {}
        improved = 0
        worsened = 0
        unchanged = 0

        for scenario_id in common_scenarios:
            delta = scores2[scenario_id] - scores1[scenario_id]
            deltas[scenario_id] = delta
            if delta > 0:
                improved += 1
            elif delta < 0:
                worsened += 1
            else:
                unchanged += 1

        total = len(common_scenarios) if common_scenarios else 1

        # Compute aggregate metrics delta
        metrics_delta: dict[str, Optional[float]] = {}
        if iter1.metrics and iter2.metrics:
            all_metric_keys = set()
            for split_metrics in iter1.metrics.values():
                if isinstance(split_metrics, dict):
                    all_metric_keys.update(split_metrics.keys())
            for key in all_metric_keys:
                val1 = self._get_metric_value(iter1.metrics, key)
                val2 = self._get_metric_value(iter2.metrics, key)
                if val1 is not None and val2 is not None:
                    metrics_delta[key] = val2 - val1
                else:
                    metrics_delta[key] = None

        return ComparisonResult(
            iteration_id_1=iteration_id_1,
            iteration_id_2=iteration_id_2,
            per_scenario_deltas=deltas,
            pct_improved=improved / total * 100,
            pct_worsened=worsened / total * 100,
            pct_unchanged=unchanged / total * 100,
            metrics_delta=metrics_delta,
        )

    def _extract_scenario_scores(self, iteration: IterationResult) -> dict[str, float]:
        """Extract per-scenario judge scores from an iteration's dev results.

        :param iteration: The iteration to extract scores from.
        :return: Dict mapping scenario (record) ID to judge score.
        """
        scores: dict[str, float] = {}
        # Prefer dev results for comparison; fall back to train
        for split in ["dev", "train"]:
            results = iteration.experiment_results.get(split)
            if results is None:
                continue
            for row in results.get("rows", []):
                record_id = row.get("record_id", "")
                judge_eval = row.get("evaluations", {}).get("judge", {})
                value = judge_eval.get("value") if isinstance(judge_eval, dict) else judge_eval
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    scores[record_id] = float(value)
            if scores:
                break
        return scores

    @staticmethod
    def _get_metric_value(metrics: dict[str, Any], key: str) -> Optional[float]:
        """Extract a metric value from the nested metrics dict.

        :param metrics: Metrics dict structured as {split: {metric_name: value}}.
        :param key: Metric name to look up.
        :return: The metric value, or None.
        """
        # Prefer dev metrics for comparison
        for split in ["dev", "train"]:
            split_metrics = metrics.get(split, {})
            if isinstance(split_metrics, dict) and key in split_metrics:
                val = split_metrics[key]
                if isinstance(val, (int, float)):
                    return float(val)
        return None

    def _find_iteration(self, iteration_id: str) -> IterationResult:
        """Find an iteration by ID.

        :param iteration_id: The iteration ID.
        :return: The matching IterationResult.
        :raises ValueError: If not found.
        """
        for iteration in self._iterations:
            if iteration.id == iteration_id:
                return iteration
        raise ValueError(f"Iteration {iteration_id} not found.")

    # ------------------------------------------------------------------
    # Prompt optimization
    # ------------------------------------------------------------------

    def run_prompt_optimization(
        self,
        optimization_task: Callable[[str, str, ConfigType], str],
        max_iterations: int = 5,
        jobs: int = 1,
    ) -> "Any":
        """Run prompt optimization to automatically improve the judge prompt.

        Delegates to the existing PromptOptimization framework, using the current
        judge config, class config, and dataset splits.

        :param optimization_task: Function that calls an LLM to generate improved prompts.
            Must accept (system_prompt, user_prompt, config) and return the new prompt string.
        :param max_iterations: Maximum optimization iterations.
        :param jobs: Parallel jobs for experiment execution.
        :return: OptimizationResult from the prompt optimization run.
        :raises ValueError: If judge config or dataset is not configured.
        """
        if self._judge_config is None:
            raise ValueError("No judge config initialized. Call init_judge() first.")
        if not self._llmobs_instance or not self._llmobs_instance.enabled:
            raise ValueError("LLMObs is not enabled.")

        from ddtrace.llmobs._prompt_optimization import PromptOptimization

        judge = self._judge_config.to_llm_judge(name="judge")
        summary_evaluators = self._build_summary_evaluators(include_metrics=True)

        # Build compute_score from class config
        def default_compute_score(summary_evals: dict[str, dict[str, Any]]) -> float:
            # Use negative MAE as score (higher is better)
            mae_data = summary_evals.get("mae", {})
            mae_val = mae_data.get("value") if isinstance(mae_data, dict) else mae_data
            if mae_val is not None and isinstance(mae_val, (int, float)):
                return -float(mae_val)
            return 0.0

        config: ConfigType = {
            "prompt": self._judge_config.prompt.content,
            "model_name": self._judge_config.model,
        }

        # Determine dataset and split settings
        dataset = self._dataset
        dataset_split: Union[bool, tuple[float, ...]] = False
        if self._split_metadata:
            dataset_split = (
                self._split_metadata.train_ratio,
                self._split_metadata.dev_ratio,
                self._split_metadata.test_ratio,
            )

        optimization = PromptOptimization(
            name=f"{self.name}_po",
            task=self._make_passthrough_task(),
            optimization_task=optimization_task,
            dataset=dataset,
            evaluators=[judge],
            project_name=self._project_name,
            config=config,
            summary_evaluators=summary_evaluators,
            compute_score=default_compute_score,
            labelization_function=None,
            _llmobs_instance=self._llmobs_instance,
            max_iterations=max_iterations,
            dataset_split=dataset_split,
        )

        result = optimization.run(jobs=jobs)

        # Update judge config with the optimized prompt
        if result.best_prompt != self._judge_config.prompt.content:
            self.update_judge(prompt=result.best_prompt)
            log.info("Judge prompt updated from prompt optimization (best iteration: %d)", result.best_iteration)

        return result

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        """Export the full project state as a JSON-serializable dict.

        Includes project metadata, judge config history, iteration history,
        metrics, and configuration.

        :return: Dict containing all project state.
        """
        iterations_export = []
        for iteration in self._iterations:
            iter_data: dict[str, Any] = {
                "id": iteration.id,
                "judge_config_id": iteration.judge_config_id,
                "status": iteration.status,
                "experiment_urls": iteration.experiment_urls,
                "metrics": iteration.metrics,
            }
            iterations_export.append(iter_data)

        export_data: dict[str, Any] = {
            "project": {
                "id": self._id,
                "name": self.name,
                "description": self._description,
                "dataset_id": self._dataset._id,
                "dataset_name": self._dataset.name,
            },
            "judge_config": self._judge_config.to_dict() if self._judge_config else None,
            "class_config": self._class_config.to_dict() if self._class_config else None,
            "alignment_config": self._alignment_config.to_dict() if self._alignment_config else None,
            "split_metadata": {
                "train_ratio": self._split_metadata.train_ratio,
                "dev_ratio": self._split_metadata.dev_ratio,
                "test_ratio": self._split_metadata.test_ratio,
                "group_column": self._split_metadata.group_column,
                "train_count": len(self._split_metadata.train_record_ids),
                "dev_count": len(self._split_metadata.dev_record_ids),
                "test_count": len(self._split_metadata.test_record_ids),
            }
            if self._split_metadata
            else None,
            "iterations": iterations_export,
        }

        # Persist via API
        if self._llmobs_instance and self._id:
            self._llmobs_instance._dne_client.eval_tuning_project_export(
                project_id=self._id,
            )

        return export_data
