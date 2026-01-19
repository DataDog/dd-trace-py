"""
Evaluator registry for the LLMObs dev server.

This module provides the infrastructure for registering and discovering evaluator functions
that can be called remotely via the dev server.
"""

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


@dataclass
class EvaluatorInfo:
    """Metadata about a registered evaluator function."""

    name: str
    function: Callable
    description: Optional[str] = None
    accepts_config: bool = field(init=False)

    def __post_init__(self):
        """Determine if the evaluator accepts a config parameter."""
        sig = inspect.signature(self.function)
        self.accepts_config = len(sig.parameters) >= 4


class EvaluatorRegistry:
    """
    Global registry for evaluators loaded from user modules.

    Evaluators are registered via the @evaluator decorator and can be discovered
    by scanning Python modules.
    """

    _evaluators: Dict[str, EvaluatorInfo] = {}

    @classmethod
    def register(
        cls,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Callable:
        """
        Decorator to register an evaluator function.

        Args:
            name: Optional name for the evaluator. Defaults to function name.
            description: Optional description of what the evaluator does.

        Returns:
            Decorator function.

        Example:
            @evaluator(name="accuracy", description="Check exact match")
            def accuracy_eval(input_data, output_data, expected_output):
                return 1 if output_data == expected_output else 0

            @evaluator(name="similarity")
            def similarity_eval(input_data, output_data, expected_output, config=None):
                threshold = config.get("threshold", 0.8) if config else 0.8
                return compute_similarity(output_data, expected_output) >= threshold
        """

        def decorator(func: Callable) -> Callable:
            eval_name = name or func.__name__
            eval_info = EvaluatorInfo(
                name=eval_name,
                function=func,
                description=description,
            )
            cls._evaluators[eval_name] = eval_info
            # Attach metadata to function for introspection
            func._evaluator_info = eval_info
            logger.debug("Registered evaluator: %s (accepts_config=%s)", eval_name, eval_info.accepts_config)
            return func

        return decorator

    @classmethod
    def get_evaluator(cls, name: str) -> Optional[EvaluatorInfo]:
        """Get an evaluator by name."""
        return cls._evaluators.get(name)

    @classmethod
    def list_evaluators(cls) -> List[EvaluatorInfo]:
        """Return all registered evaluators."""
        return list(cls._evaluators.values())

    @classmethod
    def clear(cls) -> None:
        """Clear all registered evaluators. Useful for testing."""
        cls._evaluators.clear()

    @classmethod
    def scan_module(cls, file_path: str) -> List[str]:
        """
        Import a Python module and register any @evaluator decorated functions.

        Args:
            file_path: Path to the Python file to scan.

        Returns:
            List of evaluator names that were registered from this module.

        Raises:
            FileNotFoundError: If the file does not exist.
            ImportError: If the module cannot be imported.
        """
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Module not found: {file_path}")

        if path.suffix != ".py":
            raise ValueError(f"Expected a Python file, got: {file_path}")

        # Generate a unique module name based on the file path
        module_name = f"_llmobs_evaluator_{path.stem}_{id(path)}"

        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from: {file_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        # Track evaluators before and after to find new ones
        before = set(cls._evaluators.keys())

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # Clean up on failure
            sys.modules.pop(module_name, None)
            raise ImportError(f"Error executing module {file_path}: {e}") from e

        after = set(cls._evaluators.keys())
        new_evaluators = list(after - before)

        logger.info("Loaded %d evaluator(s) from %s: %s", len(new_evaluators), file_path, new_evaluators)
        return new_evaluators


# Public decorator alias for convenience
evaluator = EvaluatorRegistry.register
