import os
import random
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import ConfigType
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import ExperimentRowResult
from ddtrace.llmobs._optimizers.optimization_iteration import OptimizationIteration


log = get_logger(__name__)


TIPS = {
    "creative": "Don't be afraid to be creative when creating the new instruction!",
    "simple": "Keep the instruction clear and concise.",
    "description": "Make sure your instruction is very informative and descriptive.",
    "specific": "The instruction should include specific details such as numbers or conditions.",
    "specificity": (
        "Be more specific in your instructions. Instead of 'handle errors', "
        "specify exactly what types of errors and how to handle them."
    ),
    "examples": "Add concrete examples to illustrate the expected format and behavior.",
    "structure": ("Use clear structure with numbered steps or bullet points to organize complex instructions."),
    "constraints": "Add explicit constraints and validation rules to prevent common mistakes.",
    "context": ("Provide more context about the domain and use case to help the model understand the task better."),
    "edge_cases": "Address edge cases and corner scenarios that might cause failures.",
    "formatting": ("Be explicit about output formatting requirements, including JSON structure and data types."),
    "clarity": "Use simpler, clearer language to reduce ambiguity in instructions.",
    "validation": "Include validation steps or self-checking mechanisms in the prompt.",
    "priorities": "Clearly state what aspects are most important when there are trade-offs.",
}


class Metaprompting(OptimizationIteration):
    """Represents a single iteration in the prompt optimization process.

    Each iteration analyzes the current prompt's performance and suggests improvements.
    """

    def __init__(
        self,
        iteration: int,
        current_prompt: str,
        current_results: ExperimentResult,
        optimization_task: Callable,
        config: ConfigType,
        labelization_function: Optional[Callable[[Dict[str, Any]], str]],
    ) -> None:
        """Initialize an optimization iteration.

        :param iteration: The iteration number (0-indexed).
        :param current_prompt: The current prompt being evaluated.
        :param current_results: Results from the previous experiment run.
        :param optimization_task: Function to generate prompt improvements.
        :param config: Configuration for the optimization task.
        :param labelization_function: Function to generate labels from individual results.
                                     Takes an individual result dict and returns a string label.
        """
        super().__init__(
            iteration=iteration,
            current_prompt=current_prompt,
            current_results=current_results,
            optimization_task=optimization_task,
            config=config,
            labelization_function=labelization_function,
        )

    def run(self) -> List[str]:
        """Run the optimization task to generate an improved prompt candidate.

        Follows the LLM-as-a-judge pattern:
        1. Loads the optimization prompt template from _prompt_optimization.md
        2. Builds user prompt with examples from evaluation results
        3. Calls optimization_task (LLM) with system and user prompts
        4. Returns a list containing the single improved prompt candidate

        :return: List containing one improved prompt string.
        """
        # Step 1: Load and prepare system prompt template
        system_prompt = self._load_system_prompt()

        # Step 2: Build user prompt with current prompt and examples
        user_prompt = self._build_user_prompt()

        # Step 3: Call optimization task
        try:
            improved_prompt = self._optimization_task(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=self._config,
            )
        except Exception as e:
            log.error(
                "Iteration %s: Failed to run optimization_task",
                self.iteration,
            )
            log.error("Exception type: %s", type(e).__name__)
            log.error("Exception message: %s", str(e))
            improved_prompt = ""

        if not improved_prompt:
            log.warning(
                "Iteration %s: optimization_task returned empty 'new_prompt', keeping current prompt",
                self.iteration,
            )
            return [self.current_prompt]

        # Step 4: Return improved prompt as a single-element list
        return [improved_prompt]

    def _load_system_prompt(self) -> str:
        """Load and prepare the optimization system prompt.

        Loads the template from _prompt_optimization.md and replaces placeholders.
        Adds evaluation model information and random tip at the end.

        :return: System prompt string with output format injected.
        """
        # Get the parent directory (ddtrace/llmobs) to find _prompt_optimization.md
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        template_path = os.path.join(parent_dir, "_prompt_optimization.md")

        # Load template
        with open(template_path, "r", encoding="utf-8") as f:
            template = f.read()

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
                    "**If this output format is not clearly specified in the initial prompt**",
                    "**add it as your first improvement step**",
                ]
            )

        system_prompt = template.replace("{{STRUCTURE_PLACEHOLDER}}", structure_placeholder)

        # Add evaluation model information at the end
        additional_parts = []
        if "model_name" in self._config:
            eval_model = self._config["model_name"]
            additional_parts.append(
                f"\n\nIMPORTANT: The improved prompt will be applied to this evaluation model: {eval_model}"
            )
            additional_parts.append(
                "Consider the capabilities, limitations, and characteristics of this specific model "
                "when optimizing the prompt."
            )

        # Add random tip at the end
        tip_key = random.choice(list(TIPS.keys()))  # nosec B311
        tip_text = TIPS[tip_key]
        additional_parts.append(f"\n\n**TIP: {tip_text}**")

        if additional_parts:
            system_prompt += "\n".join(additional_parts)

        return system_prompt

    def _add_examples(self, individual_results: List[ExperimentRowResult]) -> str:
        """Add examples of each label type using the labelization function.

        Applies the labelization function to each individual result to generate labels,
        then selects one random example for each unique label.

        :param individual_results: List of experiment result dicts.
        :return: Formatted string with examples, or empty string if no examples found.
        """
        if not individual_results or self._labelization_function is None:
            return ""

        # Step 1: Apply labelization function to each result and collect by label
        examples_by_label: Dict[str, List[ExperimentRowResult]] = {}
        for result in individual_results:
            # Cast ExperimentRowResult to Dict[str, Any] for labelization function
            label = self._labelization_function(dict(result))
            if label:  # Only add if label is not None or empty
                if label not in examples_by_label:
                    examples_by_label[label] = []
                examples_by_label[label].append(result)

        if not examples_by_label:
            return ""

        if len(examples_by_label) > 10:
            log.warning("Too many distinct labels: %s", len(examples_by_label))
            return ""

        # Step 2: Randomly select one example for each label
        examples = {}
        for label, label_examples in examples_by_label.items():
            if label_examples:
                examples[label] = random.choice(label_examples)  # nosec B311

        if not examples:
            return ""

        # Step 3: Format examples with proper headers
        formatted_parts = ["## Examples from Current Evaluation\n"]

        for label, example in sorted(examples.items(), key=lambda x: str(x[0])):
            formatted_parts.append(f"### {label}\n")
            formatted_parts.append(self._format_example(example))
            formatted_parts.append("")  # Add spacing between examples

        return "\n".join(formatted_parts)

    def _format_example(self, example: ExperimentRowResult) -> str:
        """Format an example for display in the user prompt.

        :param example: Example result dict.
        :return: Formatted string.
        """
        parts = []

        # Input
        input_data = example["input"]
        parts.append(f"Input:\n{input_data}")

        # Expected output
        expected = example["expected_output"]
        if expected:
            parts.append(f"Expected Output:\n{expected}")

        # Actual output
        output = example["output"]
        if output:
            parts.append(f"Actual Output:\n{output}")

        # Evaluation reasoning if available
        evaluations = example["evaluations"]
        if evaluations:
            for eval_name, eval_data in evaluations.items():
                if isinstance(eval_data, dict) and "reasoning" in eval_data:
                    parts.append(f"Reasoning ({eval_name}):\n{eval_data['reasoning']}")

        return "\n".join(parts)

    def _build_user_prompt(self) -> str:
        """Build user prompt with current prompt and evaluation examples.

        Includes:
        - Current prompt being optimized
        - Performance metrics
        - Examples from results (TP, TN, FP, FN if available)

        :return: User prompt string.
        """
        prompt_parts = [f"Initial Prompt:\n{self.current_prompt}\n"]

        # Extract examples from evaluation results
        results = self.current_results
        summary_evals = results.get("summary_evaluations")

        # Add performance metrics if available
        if summary_evals:
            prompt_parts.append("Performance Metrics:")
            for _, summary_metric_data in summary_evals.items():
                value = summary_metric_data.get("value", {})
                if isinstance(value, dict):
                    for metric_name, metric_data in value.items():
                        prompt_parts.append(f"- {metric_name}: {metric_data}")
            prompt_parts.append("")

        # Get individual results to find examples
        individual_results = results.get("rows", [])

        if individual_results:
            prompt_parts.append(self._add_examples(individual_results))

        final_prompt = "\n\n".join(prompt_parts)
        return final_prompt
