"""MIPRO (Multi-Instruction Prompt Optimization) optimizer implementation.

Based on the MIPRO algorithm from DSPy:
https://github.com/stanfordnlp/dspy/blob/main/dspy/teleprompt/mipro_optimizer_v2.py

MIPRO generates multiple instruction candidates and few-shot example sets,
then uses Bayesian optimization to find the optimal combination.
"""

import os
import random
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import ExperimentRowResult
from ddtrace.llmobs._optimizers.optimization_iteration import OptimizationIteration


log = get_logger(__name__)


# Prompting tips for generating diverse candidates
PROMPTING_TIPS = [
    "Be specific and detailed in your instructions.",
    "Break down complex tasks into clear, numbered steps.",
    "Provide explicit output format requirements.",
    "Include validation criteria and constraints.",
    "Address edge cases and error conditions.",
    "Use examples to illustrate the expected behavior.",
    "Specify priorities when there are trade-offs.",
    "Make the context and domain explicit.",
    "Add self-checking or verification steps.",
    "Use clear, unambiguous language.",
    "Focus on clarity over cleverness.",
    "Provide reasoning steps for complex decisions.",
    "Add explicit error handling instructions.",
    "Use consistent terminology throughout.",
    "Make assumptions explicit.",
]


# Proposer strategies for generating instructions
PROPOSER_STRATEGIES = [
    "data_aware",      # Focus on patterns from evaluation data
    "example_aware",   # Use few-shot examples to inform instructions
    "error_aware",     # Focus on fixing common errors
    "clarity_focused", # Emphasize clear communication
    "structure_focused", # Emphasize structured outputs
]


class MIPRO(OptimizationIteration):
    """MIPRO optimizer with enhanced proposer and few-shot example optimization.

    This implementation includes:
    1. Multiple instruction generation strategies (data-aware, example-aware, etc.)
    2. Few-shot example set generation and optimization
    3. Combination of instructions with different example sets
    4. Grounded proposal based on actual performance data

    The algorithm:
    1. Generate N diverse instruction candidates using multiple strategies
    2. Generate M few-shot example sets from dataset
    3. Return candidates combining instructions with example sets
    4. MIPROSelector evaluates combinations using Bayesian optimization
    """

    def __init__(
        self,
        iteration: int,
        current_prompt: str,
        current_results: ExperimentResult,
        optimization_task: Callable,
        config: ConfigType,
        labelization_function: Optional[Callable[[Dict[str, Any]], str]],
        num_instruction_candidates: int = 8,
        num_example_sets: int = 4,
        max_examples_per_set: int = 3,
        optimize_examples: bool = True,
        dataset: Optional[Dataset] = None,
    ) -> None:
        """Initialize MIPRO optimizer.

        :param iteration: The iteration number (0-indexed).
        :param current_prompt: The current prompt being evaluated.
        :param current_results: Results from the previous experiment run.
        :param optimization_task: Function to generate prompt improvements.
        :param config: Configuration for the optimization task.
        :param labelization_function: Function to generate labels from individual results.
        :param num_instruction_candidates: Number of instruction variants to generate.
        :param num_example_sets: Number of few-shot example sets to create.
        :param max_examples_per_set: Maximum examples per set.
        :param optimize_examples: Whether to optimize few-shot examples.
        :param dataset: Dataset to sample examples from.
        """
        super().__init__(
            iteration=iteration,
            current_prompt=current_prompt,
            current_results=current_results,
            optimization_task=optimization_task,
            config=config,
            labelization_function=labelization_function,
        )
        self.num_instruction_candidates = num_instruction_candidates
        self.num_example_sets = num_example_sets if optimize_examples else 1
        self.max_examples_per_set = max_examples_per_set
        self.optimize_examples = optimize_examples
        self.dataset = dataset

    def run(self) -> List[str]:
        """Generate multiple prompt candidates with instructions and examples.

        Returns a list of complete prompts, each combining:
        - An instruction variant
        - A few-shot example set (if optimize_examples=True)

        The total number of candidates = num_instruction_candidates (if not optimizing examples)
        or num_instruction_candidates * num_example_sets (if optimizing examples).

        :return: List of candidate prompt strings.
        """
        log.info(
            "MIPRO Iteration %d: Generating instruction and example candidates",
            self.iteration,
        )

        # Stage 1: Generate instruction candidates using multiple strategies
        instruction_candidates = self._generate_instruction_candidates()
        log.info("Generated %d instruction candidates", len(instruction_candidates))

        # Stage 2: Generate few-shot example sets (if enabled)
        example_sets = self._generate_example_sets()
        log.info("Generated %d example sets", len(example_sets))

        # Stage 3: Combine instructions with example sets
        full_candidates = self._combine_instructions_and_examples(
            instruction_candidates,
            example_sets,
        )
        log.info("Created %d full prompt candidates", len(full_candidates))

        return full_candidates

    def _generate_instruction_candidates(self) -> List[str]:
        """Generate diverse instruction candidates using multiple strategies.

        Uses a grounded proposer approach with multiple strategies:
        - Data-aware: Analyzes evaluation results and performance patterns
        - Example-aware: Uses successful/failed examples to inform instructions
        - Error-aware: Focuses on fixing common failure modes
        - Clarity-focused: Emphasizes clear communication
        - Structure-focused: Emphasizes structured outputs

        :return: List of instruction strings.
        """
        instructions = []
        summary_evals = self.current_results.get("summary_evaluations", {})
        individual_results = self.current_results.get("rows", [])

        # Generate instructions using different strategies
        strategies_to_use = PROPOSER_STRATEGIES[: min(len(PROPOSER_STRATEGIES), self.num_instruction_candidates)]

        for idx, strategy in enumerate(strategies_to_use):
            log.debug("Generating instruction %d using strategy: %s", idx, strategy)

            instruction = self._generate_instruction_with_strategy(
                strategy=strategy,
                candidate_num=idx,
                summary_evals=summary_evals,
                individual_results=individual_results,
            )

            if instruction:
                instructions.append(instruction)
            else:
                log.warning("Strategy %s failed, using current prompt", strategy)
                instructions.append(self.current_prompt)

        # Fill remaining slots with diverse prompting tips if needed
        while len(instructions) < self.num_instruction_candidates:
            tip = random.choice(PROMPTING_TIPS)  # nosec B311
            instruction = self._generate_instruction_with_strategy(
                strategy="tip_based",
                candidate_num=len(instructions),
                summary_evals=summary_evals,
                individual_results=individual_results,
                tip=tip,
            )
            instructions.append(instruction if instruction else self.current_prompt)

        return instructions

    def _generate_instruction_with_strategy(
        self,
        strategy: str,
        candidate_num: int,
        summary_evals: Dict[str, Dict[str, Any]],
        individual_results: List[ExperimentRowResult],
        tip: Optional[str] = None,
    ) -> str:
        """Generate a single instruction using a specific strategy.

        :param strategy: The generation strategy to use.
        :param candidate_num: Candidate number for tracking.
        :param summary_evals: Summary evaluations.
        :param individual_results: Individual results.
        :param tip: Optional prompting tip.
        :return: Generated instruction string.
        """
        # Build strategy-specific system prompt
        system_prompt = self._build_strategy_system_prompt(strategy, tip)

        # Build strategy-specific user prompt
        user_prompt = self._build_strategy_user_prompt(
            strategy=strategy,
            candidate_num=candidate_num,
            summary_evals=summary_evals,
            individual_results=individual_results,
        )

        try:
            instruction = self._optimization_task(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=self._config,
            )
            return instruction
        except Exception as e:
            log.error(
                "Instruction generation failed (strategy=%s): %s: %s",
                strategy,
                type(e).__name__,
                str(e),
            )
            return ""

    def _build_strategy_system_prompt(self, strategy: str, tip: Optional[str] = None) -> str:
        """Build system prompt based on strategy.

        :param strategy: The proposer strategy.
        :param tip: Optional prompting tip.
        :return: System prompt string.
        """
        # Load base template
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        template_path = os.path.join(parent_dir, "_prompt_optimization.md")

        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

        # Add output format if specified
        output_format = self._config.get("evaluation_output_format")
        structure_placeholder = ""
        if output_format:
            structure_placeholder = "\n".join(
                [
                    "## Prompt Output Format Requirements",
                    "The optimized prompt must guide the LLM to produce JSON output with this structure:",
                    "\n",
                    str(output_format),
                    "\n",
                    "**Make sure this format is clearly specified in the improved prompt**",
                ]
            )

        system_prompt = template.replace("{{STRUCTURE_PLACEHOLDER}}", structure_placeholder)

        # Add strategy-specific guidance
        strategy_guidance = self._get_strategy_guidance(strategy, tip)
        system_prompt += f"\n\n{strategy_guidance}"

        # Add model information
        if "model_name" in self._config:
            eval_model = self._config["model_name"]
            system_prompt += (
                f"\n\nIMPORTANT: The improved prompt will be applied to model: {eval_model}. "
                "Consider this model's capabilities when optimizing."
            )

        return system_prompt

    def _get_strategy_guidance(self, strategy: str, tip: Optional[str] = None) -> str:
        """Get strategy-specific guidance for the proposer.

        :param strategy: The proposer strategy.
        :param tip: Optional tip.
        :return: Guidance string.
        """
        guidance_map = {
            "data_aware": (
                "## OPTIMIZATION STRATEGY: Data-Aware Proposal\n"
                "Analyze the performance metrics and data patterns provided. "
                "Focus on improving areas where the current prompt struggles based on the data. "
                "Use statistical patterns and error distributions to guide your improvements."
            ),
            "example_aware": (
                "## OPTIMIZATION STRATEGY: Example-Aware Proposal\n"
                "Study the provided examples carefully, especially failed cases. "
                "Identify patterns in what works and what doesn't. "
                "Design instructions that address the failure modes you observe in the examples."
            ),
            "error_aware": (
                "## OPTIMIZATION STRATEGY: Error-Focused Proposal\n"
                "Focus specifically on common error patterns and failure modes. "
                "Design instructions that explicitly prevent these errors. "
                "Add guardrails and validation steps based on observed failures."
            ),
            "clarity_focused": (
                "## OPTIMIZATION STRATEGY: Clarity-Focused Proposal\n"
                "Prioritize crystal-clear communication. "
                "Simplify complex instructions. Remove ambiguity. "
                "Make every requirement explicit and unambiguous."
            ),
            "structure_focused": (
                "## OPTIMIZATION STRATEGY: Structure-Focused Proposal\n"
                "Emphasize clear structure and organization. "
                "Use numbered steps, bullet points, and clear sections. "
                "Make the prompt easy to follow with explicit formatting requirements."
            ),
            "tip_based": f"## OPTIMIZATION TIP: {tip}" if tip else "",
        }

        return guidance_map.get(strategy, f"## OPTIMIZATION STRATEGY: {strategy}")

    def _build_strategy_user_prompt(
        self,
        strategy: str,
        candidate_num: int,
        summary_evals: Dict[str, Dict[str, Any]],
        individual_results: List[ExperimentRowResult],
    ) -> str:
        """Build user prompt based on strategy.

        :param strategy: The proposer strategy.
        :param candidate_num: Candidate number.
        :param summary_evals: Summary evaluations.
        :param individual_results: Individual results.
        :return: User prompt string.
        """
        parts = [
            f"Current Prompt:\n{self.current_prompt}\n",
            f"Candidate #{candidate_num + 1} using {strategy} strategy",
        ]

        # Add performance metrics
        if summary_evals:
            parts.append("\nCurrent Performance Metrics:")
            for _, summary_metric_data in summary_evals.items():
                value = summary_metric_data.get("value", {})
                if isinstance(value, dict):
                    for metric_name, metric_data in value.items():
                        parts.append(f"- {metric_name}: {metric_data}")

        # Add strategy-specific examples
        if individual_results and self._labelization_function:
            if strategy == "error_aware":
                # Show mostly failed examples
                examples = self._get_examples_by_performance(individual_results, focus="failures")
            elif strategy == "example_aware":
                # Show balanced mix
                examples = self._get_examples_by_performance(individual_results, focus="balanced")
            else:
                # Show diverse examples
                examples = self._get_examples_by_performance(individual_results, focus="diverse")

            if examples:
                parts.append(examples)

        return "\n\n".join(parts)

    def _get_examples_by_performance(
        self,
        individual_results: List[ExperimentRowResult],
        focus: str = "diverse",
        max_examples: int = 5,
    ) -> str:
        """Get examples filtered by performance focus.

        :param individual_results: List of results.
        :param focus: What to focus on ("failures", "balanced", "diverse").
        :param max_examples: Maximum examples to include.
        :return: Formatted examples string.
        """
        if not self._labelization_function:
            return ""

        # Categorize examples by label
        examples_by_label: Dict[str, List[ExperimentRowResult]] = {}
        for result in individual_results:
            label = self._labelization_function(dict(result))
            if label:
                if label not in examples_by_label:
                    examples_by_label[label] = []
                examples_by_label[label].append(result)

        if not examples_by_label:
            return ""

        # Select examples based on focus
        selected_examples = []
        if focus == "failures":
            # Prioritize negative labels
            negative_labels = [lbl for lbl in examples_by_label if any(neg in lbl.lower() for neg in ["bad", "fail", "wrong", "incorrect", "error"])]
            for label in negative_labels:
                selected_examples.extend(random.sample(examples_by_label[label], min(2, len(examples_by_label[label]))))  # nosec B311
        elif focus == "balanced":
            # Equal mix from each label
            per_label = max(1, max_examples // len(examples_by_label))
            for label, examples in examples_by_label.items():
                selected_examples.extend(random.sample(examples, min(per_label, len(examples))))  # nosec B311
        else:  # diverse
            # Random mix
            all_examples = [ex for examples in examples_by_label.values() for ex in examples]
            selected_examples = random.sample(all_examples, min(max_examples, len(all_examples)))  # nosec B311

        # Format examples
        parts = ["\n## Relevant Examples from Current Evaluation\n"]
        for i, example in enumerate(selected_examples[:max_examples]):
            label = self._labelization_function(dict(example))
            parts.append(f"### Example {i + 1}: {label}")
            parts.append(self._format_example(example))
            parts.append("")

        return "\n".join(parts)

    def _format_example(self, example: ExperimentRowResult) -> str:
        """Format an example for display.

        :param example: Example result dict.
        :return: Formatted string.
        """
        parts = []

        # Input
        input_data = example.get("input")
        if input_data:
            parts.append(f"Input: {input_data}")

        # Expected output
        expected = example.get("expected_output")
        if expected:
            parts.append(f"Expected: {expected}")

        # Actual output
        output = example.get("output")
        if output:
            parts.append(f"Actual: {output}")

        # Evaluation results
        evaluations = example.get("evaluations")
        if evaluations:
            eval_parts = []
            for eval_name, eval_data in evaluations.items():
                if isinstance(eval_data, dict):
                    if "value" in eval_data:
                        eval_parts.append(f"{eval_name}={eval_data['value']}")
                    if "reasoning" in eval_data:
                        eval_parts.append(f"Reasoning: {eval_data['reasoning']}")
            if eval_parts:
                parts.append(" | ".join(eval_parts))

        return "\n".join(parts)

    def _generate_example_sets(self) -> List[List[DatasetRecord]]:
        """Generate few-shot example sets from the dataset.

        Creates diverse sets of examples that can be used as demonstrations
        in the prompt. Examples are sampled to provide diversity across
        different label categories.

        :return: List of example sets, where each set is a list of DatasetRecords.
        """
        if not self.optimize_examples or not self.dataset:
            # Return empty set if not optimizing examples
            return [[]]

        log.info("Generating %d few-shot example sets", self.num_example_sets)

        example_sets = []
        individual_results = self.current_results.get("rows", [])

        # If we have labelization function, create sets stratified by labels
        if self._labelization_function and individual_results:
            example_sets = self._generate_stratified_example_sets(individual_results)
        else:
            # Random sampling
            example_sets = self._generate_random_example_sets()

        return example_sets

    def _generate_stratified_example_sets(
        self,
        individual_results: List[ExperimentRowResult],
    ) -> List[List[DatasetRecord]]:
        """Generate example sets stratified by performance labels.

        :param individual_results: Individual results with evaluations.
        :return: List of example sets.
        """
        # Categorize results by label
        by_label: Dict[str, List[ExperimentRowResult]] = {}
        for result in individual_results:
            label = self._labelization_function(dict(result))
            if label:
                if label not in by_label:
                    by_label[label] = []
                by_label[label].append(result)

        example_sets = []
        for set_idx in range(self.num_example_sets):
            example_set = []

            # Sample from each label to ensure diversity
            labels = list(by_label.keys())
            random.shuffle(labels)  # nosec B311

            for label in labels:
                if len(example_set) >= self.max_examples_per_set:
                    break

                # Sample one example from this label
                candidates = by_label[label]
                if candidates:
                    example = random.choice(candidates)  # nosec B311

                    # Convert to DatasetRecord
                    record = DatasetRecord(
                        record_id=f"example_{set_idx}_{len(example_set)}",
                        input_data=example.get("input", {}),
                        expected_output=example.get("expected_output"),
                    )
                    example_set.append(record)

            example_sets.append(example_set)

        return example_sets

    def _generate_random_example_sets(self) -> List[List[DatasetRecord]]:
        """Generate random example sets from dataset.

        :return: List of example sets.
        """
        if not self.dataset or not self.dataset.records:
            return [[]]

        example_sets = []
        available_records = self.dataset.records[:]

        for _ in range(self.num_example_sets):
            # Shuffle for diversity
            random.shuffle(available_records)  # nosec B311

            # Sample examples
            num_examples = min(self.max_examples_per_set, len(available_records))
            example_set = available_records[:num_examples]
            example_sets.append(example_set)

        return example_sets

    def _combine_instructions_and_examples(
        self,
        instructions: List[str],
        example_sets: List[List[DatasetRecord]],
    ) -> List[str]:
        """Combine instructions with example sets to create full prompts.

        :param instructions: List of instruction strings.
        :param example_sets: List of example sets.
        :return: List of full prompt strings.
        """
        full_prompts = []

        if not self.optimize_examples or not example_sets or example_sets == [[]]:
            # If not optimizing examples, just return instructions
            return instructions

        # Create combinations of instructions and example sets
        for instruction in instructions:
            for example_set in example_sets:
                full_prompt = self._format_prompt_with_examples(instruction, example_set)
                full_prompts.append(full_prompt)

        return full_prompts

    def _format_prompt_with_examples(
        self,
        instruction: str,
        examples: List[DatasetRecord],
    ) -> str:
        """Format a prompt with few-shot examples.

        :param instruction: The instruction text.
        :param examples: List of example records.
        :return: Formatted prompt string.
        """
        if not examples:
            return instruction

        parts = [instruction, "\n## Few-Shot Examples\n"]

        for i, example in enumerate(examples):
            # Handle both dict and object access patterns
            if isinstance(example, dict):
                input_data = example.get("input_data") or example.get("input")
                expected_output = example.get("expected_output")
            else:
                input_data = getattr(example, "input_data", None) or getattr(example, "input", None)
                expected_output = getattr(example, "expected_output", None)

            parts.append(f"Example {i + 1}:")
            parts.append(f"Input: {input_data}")
            if expected_output:
                parts.append(f"Output: {expected_output}")
            parts.append("")

        return "\n".join(parts)
