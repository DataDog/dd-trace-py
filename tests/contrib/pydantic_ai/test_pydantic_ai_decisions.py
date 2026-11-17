import json
from typing import Literal
from unittest import mock

from pydantic import BaseModel
from pydantic import Field
import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


decision = pytest.importorskip("pydantic_ai.models.decision")

VCR_TYPESAFE_URL = "http://127.0.0.1:9126/vcr/typesafe"
TICKET = "Our dashboards and API calls have been timing out for 20 minutes and we have a demo in an hour."


class Triage(BaseModel):
    """Triage a support ticket."""

    urgent: bool = Field(description="Does the ticket need a reply within one hour?")
    team: Literal["billing", "engineering", "sales"] = Field(description="Which team should handle the ticket?")


class LocalDecisionModel(decision.DecisionModel):
    def __init__(self, fail=False):
        super().__init__()
        self.fail = fail
        self.requests = []

    @property
    def model_name(self):
        return "local-decider"

    @property
    def system(self):
        return "local"

    @property
    def base_url(self):
        return None

    async def decide(self, request, model_settings):
        from pydantic_ai.exceptions import ModelHTTPError
        from pydantic_ai.usage import RequestUsage

        self.requests.append(request)
        if self.fail:
            raise ModelHTTPError(status_code=500, model_name=self.model_name, body="boom")
        answers = {}
        for name, question in request.questions.items():
            if question.type == "noul":
                answers[name] = decision.NoulAnswer(noul=0.9)
            else:
                label = next(iter(question.criteria))
                answers[name] = decision.ChoiceAnswer(
                    choice=label, confidence=0.8, probabilities={k: 0.8 if k == label else 0.0 for k in question.criteria}
                )
        return decision.DecisionResponse(
            answers=answers, model_name="local-decider-1", usage=RequestUsage(input_tokens=10, output_tokens=4)
        )


class TypeSafeModel(LocalDecisionModel):
    """Named like the pydantic-ai backend so the kind rule applies, without a network call."""


def _decision_spans(test_spans):
    return [s for t in test_spans.pop_traces() for s in t if s.name == "pydantic_ai.decision"]


def _meta(span):
    return _get_llmobs_data_metastruct(span)["meta"]


@pytest.mark.parametrize("ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>")])
class TestPydanticAIDecisions:
    async def test_llm_span_without_provider_span(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        model = LocalDecisionModel()
        result = await pydantic_ai.Agent(model, output_type=Triage).run(TICKET)
        assert result.output.urgent is True
        (span,) = _decision_spans(test_spans)
        assert span.resource == "LocalDecisionModel.decide"
        meta = _meta(span)
        input_body = json.loads(meta["input"]["messages"][0]["content"])
        assert input_body["model"] == "local-decider"
        assert input_body["state"] == model.requests[0].state
        assert input_body["questions"]["urgent"]["type"] == "noul"
        assert input_body["questions"]["team"]["criteria"].keys() == {"billing", "engineering", "sales"}
        output_body = json.loads(meta["output"]["messages"][0]["content"])
        assert output_body == {
            "model": "local-decider-1",
            "answers": {
                "urgent": {"type": "noul", "noul": 0.9},
                "team": {
                    "type": "choice",
                    "choice": "billing",
                    "confidence": 0.8,
                    "probabilities": {"billing": 0.8, "engineering": 0.0, "sales": 0.0},
                },
            },
        }
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
            name="LocalDecisionModel.decide",
            model_name="local-decider-1",
            model_provider="local",
            metrics={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        )
        assert "decision_provider" not in (_get_llmobs_data_metastruct(span).get("tags") or {})

    async def test_workflow_span_when_provider_integration_enabled(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        with mock.patch.object(LLMObs, "_integration_is_enabled", side_effect=lambda name: name == "typesafe"):
            await pydantic_ai.Agent(TypeSafeModel(), output_type=Triage).run(TICKET)
        (span,) = _decision_spans(test_spans)
        data = _get_llmobs_data_metastruct(span)
        assert_llmobs_span_data(data, span_kind="workflow", name="TypeSafeModel.decide")
        assert json.loads(data["meta"]["output"]["value"])["answers"]["urgent"] == {"type": "noul", "noul": 0.9}
        assert not data.get("metrics")
        assert span.get_metric("_dd.llmobs.shadow.input_tokens") is None

    async def test_llm_span_when_provider_integration_disabled(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        with mock.patch.object(LLMObs, "_integration_is_enabled", return_value=False):
            await pydantic_ai.Agent(TypeSafeModel(), output_type=Triage).run(TICKET)
        (span,) = _decision_spans(test_spans)
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
            model_provider="typesafe",
            tags={"decision_provider": "typesafe"},
            metrics={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        )

    async def test_decide_error(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai.exceptions import ModelHTTPError

        with pytest.raises(ModelHTTPError):
            await pydantic_ai.Agent(LocalDecisionModel(fail=True), output_type=Triage).run(TICKET)
        (span,) = _decision_spans(test_spans)
        assert span.error == 1
        meta = _meta(span)
        assert meta["output"]["messages"][0]["content"] == ""
        assert not _get_llmobs_data_metastruct(span).get("metrics")

    async def test_run_stream(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        async with pydantic_ai.Agent(LocalDecisionModel(), output_type=Triage).run_stream(TICKET) as result:
            await result.get_output()
        (span,) = _decision_spans(test_spans)
        assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="llm", model_name="local-decider-1")

    async def test_system_one(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai.models.system_one import SystemOneModel
        from pydantic_ai.providers.system_one import SystemOneProvider

        import os

        provider = SystemOneProvider(base_url=VCR_TYPESAFE_URL, api_key=os.getenv("TYPESAFE_API_KEY", "not-a-real-key"))
        result = await pydantic_ai.Agent(SystemOneModel("jev-latest", provider=provider), output_type=Triage).run(TICKET)
        assert result.output.team == "engineering"
        (span,) = _decision_spans(test_spans)
        data = _get_llmobs_data_metastruct(span)
        assert_llmobs_span_data(
            data,
            span_kind="llm",
            name="SystemOneModel.decide",
            model_provider="system-one",
            tags={"decision_provider": "typesafe"},
        )
        assert data["meta"]["model_name"].startswith("jev")
        assert data["metrics"]["total_tokens"] == data["metrics"]["input_tokens"] + data["metrics"]["output_tokens"] > 0
