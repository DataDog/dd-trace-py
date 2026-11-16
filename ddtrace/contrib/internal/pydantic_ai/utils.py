from typing import Any
from typing import Optional

from ddtrace.contrib.internal.pydantic_ai._constants import PYDANTIC_AI_SYSTEM_TO_PROVIDER


def get_model_and_provider(model: Optional[Any]) -> tuple[str, Optional[str]]:
    model_name = getattr(model, "model_name", "")
    system = getattr(model, "system", None)
    if system:
        system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
    return model_name, system
