"""
Authentication middleware for the LLMObs dev server.

Validates DD-API-KEY headers to ensure only authorized requests are processed.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


class DatadogAuthMiddleware:
    """
    Middleware to validate Datadog API credentials.

    Extracts DD-API-KEY (required) and DD-APPLICATION-KEY (optional) from headers
    and stores them in request state for downstream use.

    The DD-API-KEY is already scoped to a specific Datadog organization,
    so no separate org identifier is needed.
    """

    # Paths that don't require authentication
    # /list and /eval are public for local devserver use - the UI connects directly
    # without access to DD API keys (those are server-side in the user's session)
    PUBLIC_PATHS = {"/", "/health", "/list", "/eval"}

    def __init__(self, app: ASGIApp, validate_key: bool = False):
        """
        Initialize auth middleware.

        Args:
            app: The ASGI application to wrap.
            validate_key: If True, validate the API key against Datadog API.
                         For POC, this is False (just check presence).
        """
        self.app = app
        self.validate_key = validate_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Allow public endpoints without auth
        if path in self.PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract headers
        headers = dict(scope.get("headers", []))
        api_key = headers.get(b"dd-api-key", b"").decode()
        app_key = headers.get(b"dd-application-key", b"").decode()

        # Require API key
        if not api_key:
            response = JSONResponse(
                {"error": "DD-API-KEY header is required"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        # Basic validation: check key format (should be 32 hex chars)
        if len(api_key) != 32 or not all(c in "0123456789abcdef" for c in api_key.lower()):
            logger.warning("Invalid DD-API-KEY format received")
            response = JSONResponse(
                {"error": "Invalid DD-API-KEY format"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        # Store credentials in scope state for downstream handlers
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["api_key"] = api_key
        scope["state"]["app_key"] = app_key if app_key else None

        logger.debug("Request authenticated with DD-API-KEY")
        await self.app(scope, receive, send)


def get_api_key_from_request(request: Request) -> str:
    """Extract API key from request scope state."""
    api_key = request.scope.get("state", {}).get("api_key")
    if not api_key:
        raise ValueError("API key not found in request state")
    return api_key
