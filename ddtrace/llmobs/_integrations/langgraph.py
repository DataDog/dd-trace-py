from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.span import Span

from typing import Any, Dict, List, Optional

class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ):
        pass
