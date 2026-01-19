"""
Security middleware for the LLMObs dev server.

Provides rate limiting using the existing ddtrace RateLimiter.
CORS is handled by Starlette's built-in CORSMiddleware.
"""

from typing import Dict

from starlette.responses import JSONResponse
from starlette.types import ASGIApp
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from ddtrace.internal.logger import get_logger
from ddtrace.internal.rate_limiter import RateLimiter


logger = get_logger(__name__)


class RateLimitMiddleware:
    """
    Per-API-key rate limiting middleware.

    Uses the existing ddtrace RateLimiter (token bucket implementation)
    to limit requests per API key.
    """

    def __init__(self, app: ASGIApp, requests_per_minute: int = 100):
        """
        Initialize rate limit middleware.

        Args:
            app: The ASGI application to wrap.
            requests_per_minute: Maximum requests allowed per minute per API key.
        """
        self.app = app
        self._rpm = requests_per_minute
        self._limiters: Dict[str, RateLimiter] = {}

    def _get_limiter(self, api_key: str) -> RateLimiter:
        """
        Get or create a rate limiter for an API key.

        Args:
            api_key: The DD-API-KEY value.

        Returns:
            RateLimiter instance for this key.
        """
        if api_key not in self._limiters:
            # RateLimiter uses rate_limit (requests) and time_window (nanoseconds)
            # 60 seconds = 60 * 1e9 nanoseconds
            self._limiters[api_key] = RateLimiter(
                rate_limit=self._rpm,
                time_window=60e9,  # 60 seconds in nanoseconds
            )
        return self._limiters[api_key]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Get API key from scope state (set by auth middleware)
        state = scope.get("state", {})
        api_key = state.get("api_key")

        if api_key:
            limiter = self._get_limiter(api_key)
            if not limiter.is_allowed():
                logger.warning("Rate limit exceeded for API key")
                response = JSONResponse(
                    {"error": "Rate limit exceeded. Please try again later."},
                    status_code=429,
                    headers={
                        "Retry-After": "60",
                        "X-RateLimit-Limit": str(self._rpm),
                    },
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


# CORS configuration for Datadog domains
# Use with Starlette's built-in CORSMiddleware:
#
# from starlette.middleware.cors import CORSMiddleware
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=CORS_ALLOWED_ORIGINS,
#     allow_origin_regex=CORS_ALLOWED_ORIGIN_REGEX,
#     allow_methods=CORS_ALLOWED_METHODS,
#     allow_headers=CORS_ALLOWED_HEADERS,
# )

CORS_ALLOWED_ORIGINS = [
    # Production (app.* for sites needing subdomain)
    "https://app.datadoghq.com",
    "https://app.datadoghq.eu",
    "https://app.ddog-gov.com",
    # Regional production (no app. prefix)
    "https://us3.datadoghq.com",
    "https://us5.datadoghq.com",
    # Local development
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8080",
]

# Regex pattern for Datadog domains:
# - Staging: dd.datad0g.com, dd-xxx.datad0g.com, *.us1.staging.dog
# - Production: app.datadoghq.com, app-dev-local.datadoghq.com, *.datadoghq.com
CORS_ALLOWED_ORIGIN_REGEX = r"https://.*\.(datad0g\.com|staging\.dog|datadoghq\.com)"

CORS_ALLOWED_METHODS = ["GET", "POST", "OPTIONS"]

CORS_ALLOWED_HEADERS = [
    "DD-API-KEY",
    "DD-APPLICATION-KEY",
    "Content-Type",
    "Accept",
]
