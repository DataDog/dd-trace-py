import asyncio
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Sequence
from typing import TypedDict
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._evaluators import BaseAsyncEvaluator
from ddtrace.llmobs._evaluators import BaseAsyncSummaryEvaluator
from ddtrace.llmobs._evaluators import BaseEvaluator
from ddtrace.llmobs._evaluators import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import AsyncEvaluatorType
from ddtrace.llmobs._experiment import ConfigField
from ddtrace.llmobs._experiment import EvaluatorType
from ddtrace.llmobs._experiment import Experiment
from ddtrace.llmobs._experiment import ExperimentResult
from ddtrace.llmobs._experiment import ExperimentRun
from ddtrace.llmobs._experiment import ProgressEvent


if TYPE_CHECKING:
    from ddtrace.llmobs import LLMObs

logger = get_logger(__name__)

_MAX_REQUEST_BODY_SIZE = 5 << 20  # 5MB


# TODO: Support SyncExperiment (from LLMObs.experiment()) by unwrapping to
# the inner Experiment, or unify Experiment/SyncExperiment into a single class.
class Registry:
    def __init__(self, experiments: list[Experiment]) -> None:
        self._experiments: dict[str, Experiment] = {e.name: e for e in experiments}

    def get(self, name: str) -> Optional[Experiment]:
        return self._experiments.get(name)

    def list(self) -> dict[str, Experiment]:
        return dict(self._experiments)


# -- Stream event types (devserver wire format) --


class StartEventData(TypedDict, total=False):
    experiment_name: str
    project_name: str
    experiment_id: str
    dataset_name: str
    total_rows: int


class ProgressEventData(TypedDict, total=False):
    row_index: int
    status: str
    error: Any
    evaluations: Any
    eval_metrics: Any
    span: Any


class SummaryEventData(TypedDict, total=False):
    scores: dict[str, Any]
    metrics: dict[str, Any]


class ErrorData(TypedDict, total=False):
    message: str
    type: str


class StreamEvent(TypedDict):
    event: str
    data: Any


# -- Helper functions --


def defaults_from_config(fields: dict[str, ConfigField]) -> dict[str, Any]:
    return {k: v.get("default") for k, v in fields.items() if "default" in v}


def merge_config(defaults: dict[str, Any], overrides: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not overrides:
        return dict(defaults)
    return {**defaults, **overrides}


def get_evaluator_name(evaluator: Any) -> str:
    if isinstance(evaluator, (BaseEvaluator, BaseAsyncEvaluator, BaseSummaryEvaluator, BaseAsyncSummaryEvaluator)):
        return evaluator.name
    if hasattr(evaluator, "__name__"):
        return evaluator.__name__
    return str(evaluator)


def filter_evaluators(
    all_evaluators: Sequence[Union[EvaluatorType, AsyncEvaluatorType]],
    names: Optional[list[str]],
) -> list[Union[EvaluatorType, AsyncEvaluatorType]]:
    if not names:
        return list(all_evaluators)
    name_set = set(names)
    return [e for e in all_evaluators if get_evaluator_name(e) in name_set]


def build_summary_scores(result: ExperimentResult) -> dict[str, Any]:
    scores: dict[str, list[float]] = {}
    for run in result.get("runs", []):
        if not isinstance(run, ExperimentRun):
            continue
        for row in run.rows:
            evaluations = row.get("evaluations") or {}
            for eval_name, eval_data in evaluations.items():
                if not eval_data:
                    continue
                val = eval_data.get("value")
                if isinstance(val, bool):
                    val = float(val)
                if isinstance(val, (int, float)):
                    scores.setdefault(eval_name, []).append(float(val))

    summary: dict[str, Any] = {}
    for name, vals in scores.items():
        if vals:
            summary[name] = {
                "mean": sum(vals) / len(vals),
                "count": len(vals),
                "min": min(vals),
                "max": max(vals),
            }

    # Include summary evaluations
    runs = result.get("runs", [])
    if runs and isinstance(runs[0], ExperimentRun):
        for name, eval_data in runs[0].summary_evaluations.items():
            if eval_data:
                summary[f"summary/{name}"] = eval_data.get("value")

    return summary


def _write_ndjson_line(wfile: Any, event: StreamEvent) -> None:
    line = json.dumps(event, default=str) + "\n"
    wfile.write(line.encode("utf-8"))
    wfile.flush()


class DevServerHandler(BaseHTTPRequestHandler):
    registry: Registry
    llmobs_instance: "LLMObs"
    cors_origins: Optional[list[str]] = None

    def _set_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        if self.cors_origins:
            if origin not in self.cors_origins:
                return
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json_error(self, status_code: int, message: str) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

    def end_headers(self) -> None:
        self._set_cors_headers()
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/list":
            self._handle_list()
        else:
            self._send_json_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/eval":
            self._handle_eval()
        else:
            self._send_json_error(404, "not found")

    def _handle_list(self) -> None:
        experiments = []
        for exp in self.registry.list().values():
            experiments.append(
                {
                    "name": exp.name,
                    "description": exp._description,
                    "project_name": exp._project_name,
                    "task_name": exp._task.__name__,
                    "dataset_name": exp._dataset.name,
                    "dataset_len": len(exp._dataset),
                    "evaluators": [get_evaluator_name(e) for e in exp._evaluators],
                    "config": {k: dict(v) for k, v in exp._remote_config.items()},
                    "tags": exp._user_tags,
                }
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"experiments": experiments}).encode("utf-8"))

    def _handle_eval(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json_error(400, "empty request body")
            return
        if content_length > _MAX_REQUEST_BODY_SIZE:
            self._send_json_error(413, "request body too large")
            return

        body = self.rfile.read(content_length)
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._send_json_error(400, "invalid JSON")
            return

        name = req.get("name", "")
        exp = self.registry.get(name)
        if not exp:
            self._send_json_error(404, f"experiment '{name}' not found")
            return

        stream = req.get("stream", False)
        if not isinstance(stream, bool):
            self._send_json_error(400, "'stream' must be a boolean")
            return

        sample_size = req.get("sample_size")
        if sample_size is not None:
            if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size <= 0:
                self._send_json_error(400, "'sample_size' must be a positive integer")
                return

        config_override = req.get("config_override")
        if config_override is not None and not isinstance(config_override, dict):
            self._send_json_error(400, "'config_override' must be an object")
            return

        evaluator_names = req.get("evaluators")
        if evaluator_names is not None:
            if not isinstance(evaluator_names, list) or not all(isinstance(e, str) for e in evaluator_names):
                self._send_json_error(400, "'evaluators' must be a list of strings")
                return

        config_defaults = defaults_from_config(exp._remote_config)
        merged_config = merge_config(config_defaults, config_override)
        selected_evaluators = filter_evaluators(exp._evaluators, evaluator_names)

        experiment = Experiment(
            name=exp.name,
            task=exp._task,
            dataset=exp._dataset,
            evaluators=selected_evaluators,
            project_name=exp._project_name,
            config=merged_config,
            tags=dict(exp._user_tags),
            _llmobs_instance=self.llmobs_instance,
            summary_evaluators=exp._summary_evaluators or None,
        )

        if stream:
            self._handle_eval_streaming(experiment, sample_size)
        else:
            self._handle_eval_sync(experiment, sample_size)

    def _handle_eval_sync(self, experiment: Experiment, sample_size: Optional[int]) -> None:
        try:
            result = asyncio.run(experiment.run(sample_size=sample_size))
            scores = build_summary_scores(result)
            response = {
                "scores": scores,
                "rows": result.get("rows", []),
                "summary_evaluations": result.get("summary_evaluations", {}),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response, default=str).encode("utf-8"))
        except Exception:
            logger.error("experiment eval failed", exc_info=True)
            self._send_json_error(500, "experiment execution failed")

    def _handle_eval_streaming(
        self,
        experiment: Experiment,
        sample_size: Optional[int],
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        dataset_len = len(experiment._dataset)

        def on_start(experiment_id: str, run_name: str) -> None:
            start_data: StartEventData = {
                "experiment_name": experiment.name,
                "project_name": experiment._project_name,
                "experiment_id": experiment_id,
                "dataset_name": experiment._dataset.name,
                "total_rows": dataset_len if sample_size is None else min(sample_size, dataset_len),
            }
            _write_ndjson_line(self.wfile, StreamEvent(event="start", data=start_data))

        def progress_callback(event: ProgressEvent) -> None:
            progress_data: ProgressEventData = {
                "row_index": event.get("record_index", 0),
                "status": event.get("status", ""),
            }
            if event.get("error"):
                progress_data["error"] = event["error"]
            if event.get("evaluations"):
                progress_data["evaluations"] = event["evaluations"]
            if event.get("eval_metrics"):
                progress_data["eval_metrics"] = event["eval_metrics"]
            if event.get("span_context"):
                progress_data["span"] = event["span_context"]

            _write_ndjson_line(self.wfile, StreamEvent(event="progress", data=progress_data))

        try:
            result = asyncio.run(
                experiment.run(
                    sample_size=sample_size,
                    progress_callback=progress_callback,
                    on_start=on_start,
                )
            )
            scores = build_summary_scores(result)
            _write_ndjson_line(
                self.wfile,
                StreamEvent(event="summary", data=SummaryEventData(scores=scores, metrics={})),
            )
            _write_ndjson_line(self.wfile, StreamEvent(event="done", data={}))
        except Exception:
            logger.error("streaming experiment eval failed", exc_info=True)
            _write_ndjson_line(
                self.wfile,
                StreamEvent(
                    event="error",
                    data=ErrorData(message="experiment execution failed", type="InternalError"),
                ),
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)


def run_dev_server(
    experiments: list[Experiment],
    llmobs_instance: "LLMObs",
    host: str = "0.0.0.0",
    port: int = 8787,
    cors_origins: Optional[list[str]] = None,
) -> None:
    """Blocking function that starts the devserver. Ctrl+C to stop."""
    registry = Registry(experiments)

    class Handler(DevServerHandler):
        pass

    Handler.registry = registry
    Handler.llmobs_instance = llmobs_instance
    Handler.cors_origins = cors_origins

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("devserver listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
