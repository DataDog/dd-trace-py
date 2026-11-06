from unittest.mock import Mock
from unittest.mock import patch

from openfeature.exception import ErrorCode
import pytest

from ddtrace.internal.settings.openfeature import AGENTLESS
from ddtrace.internal.settings.openfeature import DISABLED
from ddtrace.llmobs import _model_ab
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import get_llmobs_trace_id


def _details(value=None, variant=None, allocation=None, error_code=None):
    return Mock(
        error_code=error_code,
        value=value if value is not None else {},
        variant=variant,
        flag_metadata={"allocation_key": allocation} if allocation else {},
    )


@pytest.fixture
def ff_client():
    client = Mock()
    client.get_object_details.return_value = _details()
    manager = _model_ab._ModelABManager()
    with patch.object(_model_ab, "_manager", manager):
        with patch("ddtrace.internal.settings.openfeature.resolve_configuration_source", return_value=AGENTLESS):
            with patch("openfeature.api.set_provider") as set_provider:
                with patch("openfeature.api.get_client", return_value=client):
                    client.set_provider = set_provider
                    yield client


def test_flag_key_for_ml_app():
    assert _model_ab.flag_key_for_ml_app("test-app") == "__llmobs__.model.test-app"
    assert _model_ab.flag_key_for_ml_app("my app/v2") == "__llmobs__.model.my_app_v2"


def test_treatment_swaps_model_and_tags_spans(llmobs, ff_client):
    ff_client.get_object_details.return_value = _details(
        value={"model": "gpt-4.1"}, variant="gpt-4.1", allocation="ab-gpt-4o"
    )
    with llmobs.workflow("agent") as workflow:
        with llmobs.llm(model_name="gpt-4o", name="call") as llm_span:
            kwargs = {"model": "gpt-4o", "messages": []}
            _model_ab.apply_model_override(llm_span, kwargs)

    assert kwargs["model"] == "gpt-4.1"
    llm_tags = get_llmobs_tags(llm_span)
    assert llm_tags["model_ab_flag"] == "__llmobs__.model.unnamed-ml-app"
    assert llm_tags["model_ab_variant"] == "gpt-4.1"
    assert llm_tags["model_ab_allocation"] == "ab-gpt-4o"
    assert llm_tags["model_ab_original_model"] == "gpt-4o"
    assert llm_tags["model_ab_model"] == "gpt-4.1"
    workflow_tags = get_llmobs_tags(workflow)
    assert workflow_tags["model_ab_variant"] == "gpt-4.1"
    assert "model_ab_original_model" not in workflow_tags

    flag_key, default, context = ff_client.get_object_details.call_args[0]
    assert flag_key == "__llmobs__.model.unnamed-ml-app"
    assert default == {}
    assert context.attributes["model"] == "gpt-4o"
    assert context.attributes["ml_app"] == "unnamed-ml-app"


def test_control_keeps_model_but_tags_variant(llmobs, ff_client):
    ff_client.get_object_details.return_value = _details(value={}, variant="control", allocation="ab-gpt-4o")
    with llmobs.llm(model_name="gpt-4o") as llm_span:
        kwargs = {"model": "gpt-4o"}
        _model_ab.apply_model_override(llm_span, kwargs)

    assert kwargs["model"] == "gpt-4o"
    tags = get_llmobs_tags(llm_span)
    assert tags["model_ab_variant"] == "control"
    assert tags["model_ab_model"] == "gpt-4o"


@pytest.mark.parametrize(
    "details",
    [
        # No allocation targets this model: not part of any experiment.
        _details(value={}, variant="control", allocation=None),
        _details(error_code=ErrorCode.FLAG_NOT_FOUND),
        _details(error_code=ErrorCode.PROVIDER_NOT_READY),
    ],
)
def test_no_experiment_leaves_request_untouched(llmobs, ff_client, details):
    ff_client.get_object_details.return_value = details
    with llmobs.llm(model_name="gpt-4o") as llm_span:
        kwargs = {"model": "gpt-4o"}
        _model_ab.apply_model_override(llm_span, kwargs)

    assert kwargs["model"] == "gpt-4o"
    assert "model_ab_variant" not in (get_llmobs_tags(llm_span) or {})


def test_evaluation_error_leaves_request_untouched(llmobs, ff_client):
    ff_client.get_object_details.side_effect = RuntimeError("boom")
    with llmobs.llm(model_name="gpt-4o") as llm_span:
        kwargs = {"model": "gpt-4o"}
        _model_ab.apply_model_override(llm_span, kwargs)
    assert kwargs["model"] == "gpt-4o"


def test_assignment_is_fixed_within_a_trace(llmobs, ff_client):
    # Only two responses: a second evaluation for gpt-4o would consume the gpt-4o-mini one.
    ff_client.get_object_details.side_effect = [
        _details(value={"model": "gpt-4.1"}, variant="gpt-4.1", allocation="ab-gpt-4o"),
        _details(value={"model": "gpt-4.1-mini"}, variant="gpt-4.1-mini", allocation="ab-gpt-4o-mini"),
    ]
    models = []
    with llmobs.workflow("agent"):
        for original in ("gpt-4o", "gpt-4o", "gpt-4o-mini"):
            with llmobs.llm(model_name=original) as llm_span:
                kwargs = {"model": original}
                _model_ab.apply_model_override(llm_span, kwargs)
                models.append(kwargs["model"])

    assert models == ["gpt-4.1", "gpt-4.1", "gpt-4.1-mini"]
    # One evaluation per original model in the trace.
    assert ff_client.get_object_details.call_count == 2


def test_targeting_key_prefers_session_id(llmobs, ff_client):
    with llmobs.workflow("agent", session_id="session-123"):
        with llmobs.llm(model_name="gpt-4o") as llm_span:
            _model_ab.apply_model_override(llm_span, {"model": "gpt-4o"})
    context = ff_client.get_object_details.call_args[0][2]
    assert context.targeting_key == "session-123"


def test_targeting_key_falls_back_to_llmobs_trace_id(llmobs, ff_client):
    with llmobs.llm(model_name="gpt-4o") as llm_span:
        _model_ab.apply_model_override(llm_span, {"model": "gpt-4o"})
    context = ff_client.get_object_details.call_args[0][2]
    assert context.targeting_key == get_llmobs_trace_id(llm_span)


def test_start_registers_provider_on_own_domain_once(ff_client):
    _model_ab.start()
    _model_ab.start()
    assert ff_client.set_provider.call_count == 1
    assert ff_client.set_provider.call_args[0][1] == _model_ab.FFE_DOMAIN


def test_start_skips_provider_when_feature_flags_disabled():
    manager = _model_ab._ModelABManager()
    with patch("ddtrace.internal.settings.openfeature.resolve_configuration_source", return_value=DISABLED):
        with patch("openfeature.api.set_provider") as set_provider:
            manager.start()
    set_provider.assert_not_called()
