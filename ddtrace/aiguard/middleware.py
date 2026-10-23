"""Customer middlewares that filter and observe every AI Guard evaluation."""

from typing import Any
from typing import Optional


__all__ = ["AIGuardContext", "AIGuardMiddleware", "register_middleware"]


class AIGuardContext:
    """The evaluation a middleware hook is called for, shared by every hook of that evaluation.

    :ivar messages: The conversation being evaluated. Do not mutate it.
    :ivar options: The evaluation options, or None.
    :ivar source: "sdk" for a direct evaluate call, "auto" for auto-instrumentation.
    :ivar integration: The auto-instrumented AI package, for example "openai".
    :ivar tags: The custom tags given to evaluate. Tag the span with context.span.set_tag.
    :ivar span: The AI Guard span; None in should_evaluate since it does not exist yet.
    :ivar result: The Evaluation once the service answered, including when blocked.
    :ivar error: The exception evaluate is about to raise, if any.
    """

    def __init__(
        self,
        messages: list[Any],
        options: Optional[Any],
        source: str,
        integration: str,
        tags: Optional[dict[str, Any]],
    ):
        self.messages = messages
        self.options = options
        self.source = source
        self.integration = integration
        self.tags = tags
        self.span: Optional[Any] = None
        self.result: Optional[Any] = None
        self.error: Optional[BaseException] = None


class AIGuardMiddleware:
    """Base class for hooks run around every AI Guard evaluation, SDK and auto-instrumentation alike.

    Register an instance with register_middleware. Middlewares run in registration order, and an
    exception raised by a hook is logged at debug level and ignored.

    Example::

        from ddtrace.aiguard.middleware import AIGuardMiddleware, register_middleware

        class SkipPings(AIGuardMiddleware):
            def should_evaluate(self, context):
                return context.messages[-1].get("content") != "ping"

        register_middleware(SkipPings())
    """

    def should_evaluate(self, context: AIGuardContext) -> Optional[bool]:
        """Return False to skip the evaluation: no span, no request, and an ALLOW result."""
        return None

    def pre_evaluate(self, context: AIGuardContext) -> None:
        """Called inside the AI Guard span, right before the request to the service."""

    def post_evaluate(self, context: AIGuardContext) -> None:
        """Called once when the evaluation finishes, whether it allows, denies, blocks or fails.

        Not called when should_evaluate skipped the evaluation.
        """


_MIDDLEWARES: list[AIGuardMiddleware] = []


def register_middleware(middleware: AIGuardMiddleware) -> None:
    """Run middleware around every subsequent AI Guard evaluation in this process."""
    _MIDDLEWARES.append(middleware)
