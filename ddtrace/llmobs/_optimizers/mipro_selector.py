"""MIPRO Candidate Selector using Bayesian Optimization.

This selector evaluates prompt candidates using Optuna's Tree-structured Parzen
Estimator (TPE) for intelligent exploration of the candidate space.
"""

from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._optimizers.candidate_selector import CandidateSelector


log = get_logger(__name__)


class MIPROSelector(CandidateSelector):
    """Candidate selector for MIPRO optimizer using Bayesian optimization.

    Instead of evaluating all candidates sequentially, this selector uses
    Optuna's TPE sampler to intelligently explore the candidate space:
    1. Samples candidates based on Bayesian priors
    2. Evaluates them and observes scores
    3. Updates the probabilistic model
    4. Suggests next candidates to try
    5. Returns the best after num_trials evaluations

    This is more efficient than exhaustive search when you have many candidates.
    """

    def select_best_candidate(
        self,
        candidates: List[str],
        iteration: int,
        experiment_name: str,
        jobs: int,
    ) -> Tuple[str, ExperimentResult, str]:
        """Select best candidate using Bayesian optimization.

        :param candidates: List of candidate prompt strings.
        :param iteration: The iteration number.
        :param experiment_name: Base name for experiments.
        :param jobs: Number of parallel jobs for experiment execution.
        :return: Tuple of (best_prompt, best_results, experiment_url).
        :raises ValueError: If no candidates provided.
        """
        if not candidates:
            raise ValueError("No candidates provided for selection")

        num_candidates = len(candidates)
        log.info(
            "MIPROSelector: Using Bayesian optimization to select from %d candidates",
            num_candidates,
        )

        # For small number of candidates, evaluate all
        # For larger numbers, use Bayesian optimization
        if num_candidates <= 3:
            return self._evaluate_all_candidates(
                candidates=candidates,
                experiment_name=experiment_name,
                jobs=jobs,
            )
        else:
            return self._bayesian_optimization(
                candidates=candidates,
                iteration=iteration,
                experiment_name=experiment_name,
                jobs=jobs,
                num_trials=min(num_candidates, max(10, num_candidates // 2)),
            )

    def _evaluate_all_candidates(
        self,
        candidates: List[str],
        experiment_name: str,
        jobs: int,
    ) -> Tuple[str, ExperimentResult, str]:
        """Evaluate all candidates and return the best.

        :param candidates: List of candidate prompts.
        :param experiment_name: Experiment name.
        :param jobs: Number of parallel jobs.
        :return: Tuple of (best_prompt, best_results, experiment_url).
        """
        log.info("Evaluating all %d candidates", len(candidates))

        best_prompt = None
        best_results = None
        best_url = None
        best_score = float("-inf")

        model_name = self._config.get("model_name")
        runs_value = self._config.get("runs")
        runs_int: Optional[int] = None
        if runs_value is not None and isinstance(runs_value, int):
            runs_int = runs_value

        for idx, candidate in enumerate(candidates):
            log.info("Evaluating candidate %d/%d", idx + 1, len(candidates))

            # Run experiment on this candidate
            results, url = self._run_experiment(
                prompt=candidate,
                experiment_name=f"{experiment_name}_candidate_{idx}",
                model_name=model_name,
                jobs=jobs,
                runs=runs_int,
            )

            # Compute score
            summary_evals = results.get("summary_evaluations", {})
            score = self._compute_score(summary_evals)

            log.info("Candidate %d score: %.4f", idx, score)

            # Track best
            if score > best_score:
                best_score = score
                best_prompt = candidate
                best_results = results
                best_url = url
                log.info("New best candidate! Score: %.4f", score)

        return best_prompt, best_results, best_url

    def _bayesian_optimization(
        self,
        candidates: List[str],
        iteration: int,
        experiment_name: str,
        jobs: int,
        num_trials: int,
    ) -> Tuple[str, ExperimentResult, str]:
        """Use Bayesian optimization to select the best candidate.

        Uses Optuna's TPE (Tree-structured Parzen Estimator) sampler to
        intelligently explore the candidate space without evaluating all.

        :param candidates: List of candidate prompts.
        :param iteration: Iteration number.
        :param experiment_name: Experiment name.
        :param jobs: Number of parallel jobs.
        :param num_trials: Number of trials to run.
        :return: Tuple of (best_prompt, best_results, experiment_url).
        """
        try:
            import optuna
        except ImportError:
            log.warning(
                "Optuna not installed. Falling back to exhaustive evaluation. "
                "Install optuna for more efficient candidate selection: pip install optuna"
            )
            return self._evaluate_all_candidates(candidates, experiment_name, jobs)

        # Suppress optuna logging
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        log.info(
            "Using Bayesian optimization: %d trials for %d candidates",
            num_trials,
            len(candidates),
        )

        # Track best candidate found
        best_prompt = None
        best_results = None
        best_url = None
        best_score = float("-inf")

        # Track which candidates have been evaluated
        evaluated_candidates = {}

        model_name = self._config.get("model_name")
        runs_value = self._config.get("runs")
        runs_int: Optional[int] = None
        if runs_value is not None and isinstance(runs_value, int):
            runs_int = runs_value

        def objective(trial: optuna.trial.Trial) -> float:
            """Optuna objective function.

            :param trial: Optuna trial object.
            :return: Score for this trial.
            """
            nonlocal best_prompt, best_results, best_url, best_score

            # Sample a candidate index
            candidate_idx = trial.suggest_int("candidate_idx", 0, len(candidates) - 1)

            # Check if already evaluated
            if candidate_idx in evaluated_candidates:
                log.info(
                    "Trial %d: Re-using score for candidate %d",
                    trial.number,
                    candidate_idx,
                )
                return evaluated_candidates[candidate_idx]["score"]

            candidate = candidates[candidate_idx]

            log.info(
                "Trial %d/%d: Evaluating candidate %d",
                trial.number + 1,
                num_trials,
                candidate_idx,
            )

            # Run experiment
            results, url = self._run_experiment(
                prompt=candidate,
                experiment_name=f"{experiment_name}_trial_{trial.number}",
                model_name=model_name,
                jobs=jobs,
                runs=runs_int,
            )

            # Compute score
            summary_evals = results.get("summary_evaluations", {})
            score = self._compute_score(summary_evals)

            log.info("Trial %d: Candidate %d score: %.4f", trial.number, candidate_idx, score)

            # Cache result
            evaluated_candidates[candidate_idx] = {
                "prompt": candidate,
                "results": results,
                "url": url,
                "score": score,
            }

            # Update best if improved
            if score > best_score:
                best_score = score
                best_prompt = candidate
                best_results = results
                best_url = url
                log.info("New best candidate! Score: %.4f", score)

            return score

        # Create Optuna study with TPE sampler
        sampler = optuna.samplers.TPESampler(seed=42, multivariate=True)
        study = optuna.create_study(direction="maximize", sampler=sampler)

        # Run optimization
        study.optimize(objective, n_trials=num_trials, show_progress_bar=False)

        # Log summary
        log.info("Bayesian optimization complete:")
        log.info("- Evaluated %d unique candidates", len(evaluated_candidates))
        log.info("- Best score: %.4f", best_score)

        return best_prompt, best_results, best_url
