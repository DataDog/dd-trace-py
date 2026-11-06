"""Online model A/B testing driven by Datadog Feature Flags.

When DD_AO_AB_ENABLED is set, LLM integrations ask this module whether the model on an outgoing request
should be swapped. Each ml_app has one JSON flag, __llmobs__.model.<ml_app>. Each experiment on that flag is an
allocation targeted at one original model (the "model" evaluation attribute); its variants carry
{"model": "<replacement>"}, and the control variant is {} (keep the original).

The assignment is keyed on the session id when one is set, else the LLMObs trace id, and is cached on the
local root span so every call in a trace sees the same model even if a session id appears mid-trace.
"""

import re
from typing import Any
from typing import NamedTuple
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import Lock
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import get_llmobs_ml_app
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.trace import Span


log = get_logger(__name__)

FLAG_KEY_PREFIX = "__llmobs__.model."
FFE_DOMAIN = "datadog-llmobs-models"

TAG_FLAG = "model_ab_flag"
TAG_VARIANT = "model_ab_variant"
TAG_ALLOCATION = "model_ab_allocation"
TAG_ORIGINAL_MODEL = "model_ab_original_model"
TAG_MODEL = "model_ab_model"

# Per-trace assignments, keyed by original model, stored on the local root APM span.
_ASSIGNMENTS_CTX_KEY = "_ml_obs.model_ab.assignments"

_INVALID_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


class Assignment(NamedTuple):
    flag_key: str
    original_model: str
    model: str
    variant: Optional[str]
    allocation: Optional[str]


def flag_key_for_ml_app(ml_app: str) -> str:
    # Must match the key web-ui builds (llm-model-ab-test toolkit).
    return FLAG_KEY_PREFIX + _INVALID_KEY_CHARS.sub("_", ml_app)


class _ModelABManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._started = False

    def start(self) -> None:
        """Register the OpenFeature provider so flag configuration starts loading before the first request."""
        with self._lock:
            if self._started:
                return
            self._started = True
            from ddtrace.internal.settings import openfeature as ffe_settings

            source = ffe_settings.resolve_configuration_source(ffe_settings.config)
            if source == ffe_settings.DISABLED:
                log.warning("DD_AO_AB_ENABLED is set but Feature Flagging is disabled; model A/B tests will not run")
                return
            try:
                if source == ffe_settings.REMOTE_CONFIG:
                    from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

                    enable_featureflags_rc()

                from openfeature import api

                from ddtrace.internal.openfeature._provider import DataDogProvider

                # Own domain so the application's default OpenFeature provider is left alone.
                api.set_provider(DataDogProvider(initialization_timeout=0), FFE_DOMAIN)
            except ImportError:
                log.warning("DD_AO_AB_ENABLED requires the openfeature-sdk package; model A/B tests will not run")
            except Exception:
                log.debug("Failed to register OpenFeature provider for model A/B tests", exc_info=True)

    def resolve(self, span: Span, model: str) -> Optional[Assignment]:
        """Return the model assignment for an LLM span about to call model, or None to leave it untouched."""
        root = span._local_root
        assignments: Optional[dict[str, Optional[Assignment]]] = root._get_ctx_item(_ASSIGNMENTS_CTX_KEY)
        if assignments is None:
            assignments = {}
            root._set_ctx_item(_ASSIGNMENTS_CTX_KEY, assignments)
        if model in assignments:
            return assignments[model]

        assignment = self._evaluate(span, model)
        assignments[model] = assignment
        return assignment

    def _evaluate(self, span: Span, model: str) -> Optional[Assignment]:
        ml_app = get_llmobs_ml_app(span)
        if not ml_app:
            return None
        self.start()
        try:
            from openfeature import api
            from openfeature.evaluation_context import EvaluationContext
        except ImportError:
            return None

        flag_key = flag_key_for_ml_app(ml_app)
        targeting_key = get_llmobs_session_id(span) or get_llmobs_trace_id(span) or str(span.trace_id)
        context = EvaluationContext(
            targeting_key=targeting_key,
            attributes={"ml_app": ml_app, "model": model, "service": config.service or ""},
        )
        try:
            details = api.get_client(FFE_DOMAIN).get_object_details(flag_key, {}, context)
        except Exception:
            log.debug("Model A/B flag evaluation failed for %s", flag_key, exc_info=True)
            return None

        if details.error_code is not None or not isinstance(details.value, dict):
            return None
        replacement = details.value.get("model")
        metadata = details.flag_metadata or {}
        allocation = metadata.get("allocation_key")
        if not allocation:
            # No allocation matched this model, so this request is not part of any experiment.
            return None
        return Assignment(
            flag_key=flag_key,
            original_model=model,
            model=replacement if isinstance(replacement, str) and replacement else model,
            variant=details.variant,
            allocation=str(allocation),
        )


_manager = _ModelABManager()


def start() -> None:
    _manager.start()


def apply_model_override(span: Span, kwargs: dict[str, Any]) -> None:
    """Swap kwargs["model"] for the assigned variant and tag the LLM span and its LLMObs ancestors.

    Never raises: a failure here must not break the user's LLM call.
    """
    model = kwargs.get("model")
    if not isinstance(model, str) or not model:
        return
    try:
        assignment = _manager.resolve(span, model)
        if assignment is None:
            return
        kwargs["model"] = assignment.model
        _tag_assignment(span, assignment)
    except Exception:
        log.debug("Error applying model A/B assignment", exc_info=True)


def _tag_assignment(span: Span, assignment: Assignment) -> None:
    tags = {
        TAG_FLAG: assignment.flag_key,
        TAG_VARIANT: assignment.variant or "",
        TAG_ALLOCATION: assignment.allocation or "",
        TAG_ORIGINAL_MODEL: assignment.original_model,
        TAG_MODEL: assignment.model,
    }
    _annotate_llmobs_span_data(span, tags=tags)
    # Tag the enclosing LLMObs spans too, so trace-level evals and outcomes can be split by variant.
    # Original/assigned model stay on the LLM span only; a trace may run several experiments.
    ancestor_tags = {TAG_FLAG: tags[TAG_FLAG], TAG_VARIANT: tags[TAG_VARIANT], TAG_ALLOCATION: tags[TAG_ALLOCATION]}
    ancestor = _get_nearest_llmobs_ancestor(span)
    while ancestor is not None:
        _annotate_llmobs_span_data(ancestor, tags=ancestor_tags)
        ancestor = _get_nearest_llmobs_ancestor(ancestor)
