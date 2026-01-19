"""
HTTP server for the LLMObs dev server.

Provides a Starlette application with routes for:
- GET /        - Health check
- GET /list    - List registered evaluators
- POST /eval   - Execute an evaluation
"""

from typing import List
from typing import Optional

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.devserver.auth import DatadogAuthMiddleware
from ddtrace.llmobs.devserver.middleware import CORS_ALLOWED_HEADERS
from ddtrace.llmobs.devserver.middleware import CORS_ALLOWED_METHODS
from ddtrace.llmobs.devserver.middleware import CORS_ALLOWED_ORIGIN_REGEX
from ddtrace.llmobs.devserver.middleware import CORS_ALLOWED_ORIGINS
from ddtrace.llmobs.devserver.middleware import RateLimitMiddleware
from ddtrace.llmobs.devserver.registry import EvaluatorRegistry
from ddtrace.llmobs.devserver.schemas import EvaluatorListItem
from ddtrace.llmobs.devserver.schemas import ListResponse
from ddtrace.llmobs.devserver.schemas import ValidationError
from ddtrace.llmobs.devserver.schemas import error_response
from ddtrace.llmobs.devserver.schemas import parse_eval_request
from ddtrace.llmobs.devserver.schemas import result_to_eval_response


logger = get_logger(__name__)


async def health_check(request: Request) -> JSONResponse:
    """
    Health check endpoint.

    GET /
    Returns: {"status": "ok"}
    """
    return JSONResponse({"status": "ok"})


async def list_evaluators(request: Request) -> JSONResponse:
    """
    List all registered evaluators.

    GET /list
    Returns: {"evaluators": [{"name": "...", "description": "...", "accepts_config": bool}, ...]}
    """
    evaluators = EvaluatorRegistry.list_evaluators()
    items: List[EvaluatorListItem] = [
        {
            "name": e.name,
            "description": e.description,
            "accepts_config": e.accepts_config,
        }
        for e in evaluators
    ]
    response: ListResponse = {"evaluators": items}
    return JSONResponse(response)


async def run_evaluation(request: Request) -> JSONResponse:
    """
    Execute an evaluation.

    POST /eval
    Body: {
        "evaluator_name": "accuracy",
        "input_data": {"question": "What is 2+2?"},
        "output_data": "4",
        "expected_output": "4",
        "config": {"threshold": 0.8}  // optional
    }
    Returns: {
        "label": "accuracy",
        "metric_type": "score",
        "score_value": 1.0,
        "timestamp_ms": 1705600000000
    }
    """
    # Parse request body
    try:
        body_bytes = await request.body()
        eval_request = parse_eval_request(body_bytes)
    except ValidationError as e:
        logger.warning("Invalid eval request: %s", e)
        return JSONResponse({"error": str(e)}, status_code=400)

    evaluator_name = eval_request["evaluator_name"]

    # Get evaluator from registry
    evaluator_info = EvaluatorRegistry.get_evaluator(evaluator_name)
    if evaluator_info is None:
        logger.warning("Evaluator not found: %s", evaluator_name)
        return JSONResponse(
            {"error": f"Evaluator '{evaluator_name}' not found"},
            status_code=404,
        )

    # Execute evaluator
    try:
        input_data = eval_request["input_data"]
        output_data = eval_request["output_data"]
        expected_output = eval_request["expected_output"]
        config = eval_request.get("config")

        # Call evaluator with or without config based on its signature
        if evaluator_info.accepts_config and config is not None:
            result = evaluator_info.function(input_data, output_data, expected_output, config)
        else:
            result = evaluator_info.function(input_data, output_data, expected_output)

        # Convert tags dict to list format if provided
        tags_list: Optional[List[str]] = None
        if eval_request.get("tags"):
            tags_list = [f"{k}:{v}" for k, v in eval_request["tags"].items()]

        # Convert result to response format
        response = result_to_eval_response(evaluator_name, result, tags_list)
        metric_type = response.get("metric_type")
        value = response.get(f"{metric_type}_value")
        logger.info("Evaluation completed: %s -> %s=%s", evaluator_name, metric_type, value)
        return JSONResponse(response)

    except Exception as e:
        logger.exception("Evaluation failed: %s", evaluator_name)
        response = error_response(evaluator_name, str(e))
        return JSONResponse(response, status_code=500)


def create_app(
    requests_per_minute: int = 100,
    cors_origins: Optional[List[str]] = None,
) -> Starlette:
    """
    Create the Starlette application with all middleware and routes.

    Args:
        requests_per_minute: Rate limit per API key (default: 100).
        cors_origins: List of allowed CORS origins. Defaults to Datadog domains.

    Returns:
        Configured Starlette application.
    """
    routes = [
        Route("/", health_check, methods=["GET"]),
        Route("/health", health_check, methods=["GET"]),
        Route("/list", list_evaluators, methods=["GET"]),
        Route("/eval", run_evaluation, methods=["POST"]),
    ]

    app = Starlette(routes=routes)

    # Add middleware (order matters - outermost first)
    # 1. CORS - handle preflight requests first
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or CORS_ALLOWED_ORIGINS,
        allow_origin_regex=CORS_ALLOWED_ORIGIN_REGEX,
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=CORS_ALLOWED_HEADERS,
        allow_credentials=False,
    )

    # 2. Rate limiting - applied after CORS, before auth
    app.add_middleware(RateLimitMiddleware, requests_per_minute=requests_per_minute)

    # 3. Authentication - innermost, validates credentials
    app.add_middleware(DatadogAuthMiddleware)

    logger.info("Created LLMObs dev server application")
    return app
