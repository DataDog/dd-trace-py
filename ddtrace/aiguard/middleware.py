"""Customer middlewares that filter and observe every AI Guard evaluation."""

from enum import Enum
from typing import Any
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.aiguard._types import Evaluation
from ddtrace.aiguard._types import Message


__all__ = [
    "AIGuardContext",
    "AIGuardMiddleware",
    "AIGuardRequest",
    "EvaluationDirection",
    "EvaluationTarget",
    "MiddlewareRegistration",
    "register_middleware",
]


class EvaluationDirection(str, Enum):
    """Whether the evaluated messages go into the LLM operation or come out of it."""

    INPUT = "input"
    OUTPUT = "output"


class EvaluationTarget(str, Enum):
    """Whether the evaluation is about a prompt or a tool call."""

    PROMPT = "prompt"
    TOOL = "tool"


class AIGuardRequest:
    """What is about to be evaluated.

    :ivar source: "sdk" for a direct evaluate call, "auto" for auto-instrumentation.
    :ivar integration: The auto-instrumented AI package, for example "openai", or "none".
    :ivar direction: An EvaluationDirection: OUTPUT when the last message comes from the assistant.
    :ivar target: An EvaluationTarget: TOOL for a tool call or tool result, PROMPT otherwise.
    :ivar messages: The complete ordered list of messages to evaluate. Do not mutate it.
    """

    def __init__(
        self,
        source: str,
        integration: str,
        direction: EvaluationDirection,
        target: EvaluationTarget,
        messages: list[Message],
    ):
        self.source = source
        self.integration = integration
        self.direction = direction
        self.target = target
        self.messages = messages


class AIGuardContext:
    """The evaluation a middleware hook is called for, shared by every hook of that evaluation.

    :ivar local_root_span: The local root span of the surrounding application trace, or None.
    :ivar ai_guard_span: The AI Guard span, with its core tags already set. None in
        should_evaluate, since the span does not exist yet.
    :ivar request: The AIGuardRequest being evaluated.
    :ivar result: The Evaluation once the service answered, including when blocked.
    :ivar error: The exception evaluate is about to raise, if any.
    :ivar state: Free-form values a middleware keeps between the hooks of one evaluation.
    """

    def __init__(self, request: AIGuardRequest, local_root_span: Optional[Span] = None):
        self.local_root_span = local_root_span
        self.ai_guard_span: Optional[Span] = None
        self.request = request
        self.result: Optional[Evaluation] = None
        self.error: Optional[BaseException] = None
        self.state: dict[str, Any] = {}


class AIGuardMiddleware:
    """Base class for hooks run around every AI Guard evaluation, SDK and auto-instrumentation alike.

    Register an instance with register_middleware. Middlewares run in registration order, and an
    exception raised by a hook is logged at debug level and ignored.

    Example::

        from ddtrace.aiguard.middleware import AIGuardMiddleware, EvaluationDirection, register_middleware

        class InputOnly(AIGuardMiddleware):
            def should_evaluate(self, context):
                return context.request.direction is EvaluationDirection.INPUT

        registration = register_middleware(InputOnly())
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


class MiddlewareRegistration:
    """Handle returned by register_middleware."""

    def __init__(self, middleware: AIGuardMiddleware):
        self._middleware: Optional[AIGuardMiddleware] = middleware

    def unregister(self) -> None:
        """Stop running the middleware. Calling it again has no effect."""
        # Forget the middleware so a second call cannot remove another registration of it.
        target, self._middleware = self._middleware, None
        for index, middleware in enumerate(_MIDDLEWARES):
            if middleware is target:
                del _MIDDLEWARES[index]
                return


def register_middleware(middleware: AIGuardMiddleware) -> MiddlewareRegistration:
    """Run middleware around every subsequent AI Guard evaluation in this process.

    :returns: A MiddlewareRegistration whose unregister method removes the middleware.
    """
    _MIDDLEWARES.append(middleware)
    return MiddlewareRegistration(middleware)
