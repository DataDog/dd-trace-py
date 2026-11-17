import json
import os
from typing import Literal

from pydantic import BaseModel
from pydantic import Field
from pydantic import TypeAdapter
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


decision = pytest.importorskip("pydantic_ai.models.decision")
Answer = pytest.importorskip("openai.types.decision").Answer

VCR_URL = "http://127.0.0.1:9126/vcr"
TICKET = "Our dashboards and API calls have been timing out for 20 minutes and we have a demo in an hour."


class Triage(BaseModel):
    """Triage a support ticket."""

    urgent: bool = Field(description="Does the ticket need a reply within one hour?")
    team: Literal["billing", "engineering", "sales"] = Field(description="Which team should handle the ticket?")


def typesafe_model(api_key=None):
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider

    api_key = api_key or os.getenv("TYPESAFE_API_KEY", "not-a-real-key")
    return TypeSafeModel("jev-latest", provider=TypeSafeProvider(base_url=f"{VCR_URL}/typesafe", api_key=api_key))


def system_one_model(api_key=None):
    from pydantic_ai.models.system_one import SystemOneModel
    from pydantic_ai.providers.system_one import SystemOneProvider

    api_key = api_key or os.getenv("TYPESAFE_API_KEY", "not-a-real-key")
    return SystemOneModel("jev-latest", provider=SystemOneProvider(base_url=f"{VCR_URL}/typesafe", api_key=api_key))


def openai_model(api_key=None):
    from pydantic_ai.models.openai_decisions import OpenAIDecisionsModel
    from pydantic_ai.providers.openai import OpenAIProvider

    api_key = api_key or os.getenv("OPENAI_API_KEY", "not-a-real-key")
    return OpenAIDecisionsModel("gpt-6-luna", provider=OpenAIProvider(base_url=f"{VCR_URL}/openai", api_key=api_key))


def google_model(api_key=None):
    from google.genai import Client
    from google.genai.types import HttpOptions
    from pydantic_ai.models.google_decisions import GoogleDecisionsModel
    from pydantic_ai.providers.google import GoogleProvider

    client = Client(
        api_key=api_key or os.getenv("GEMINI_API_KEY", "not-a-real-key"),
        http_options=HttpOptions(base_url=f"{VCR_URL}/genai", api_version="v1beta"),
    )
    return GoogleDecisionsModel("gemini-decision-1", provider=GoogleProvider(client=client))


async def run_triage(model):
    from pydantic_ai import Agent

    return await Agent(model, output_type=Triage).run(TICKET)


class LocalDecisionModel(decision.DecisionModel):
    """A backend without a provider span; its answers are computed locally."""

    def __init__(self, fail=False, max_questions=None):
        super().__init__()
        self.fail = fail
        self.max_questions = max_questions
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
                labels = list(question.criteria)
                label = next((label for label in labels if "lookup" not in label), labels[0])
                answers[name] = decision.ChoiceAnswer(
                    choice=label, confidence=0.8, probabilities={k: 0.8 if k == label else 0.0 for k in labels}
                )
        return decision.DecisionResponse(
            answers=answers, model_name="local-decider-1", usage=RequestUsage(input_tokens=10, output_tokens=4)
        )


def _recorded_openai_answers():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "cassettes", "openai", "openai_decisions_post_6bde0ba7.json"
    )
    with open(path) as f:
        body = json.load(f)["response"]["body"]
    return (json.loads(body) if isinstance(body, str) else body)["answers"]


def _spans(test_spans):
    return [s for t in test_spans.pop_traces() for s in t]


def _decision_spans(spans):
    return [s for s in spans if s.name == "pydantic_ai.decision"]


def _llmobs(span):
    return _get_llmobs_data_metastruct(span)


def _total_tokens(spans):
    return sum((_llmobs(s).get("metrics") or {}).get("total_tokens", 0) for s in spans if _llmobs(s))


@pytest.fixture
def typesafe_patched(monkeypatch):
    import ddtrace._monkey
    from ddtrace.contrib.internal.typesafe.patch import patch
    from ddtrace.contrib.internal.typesafe.patch import unpatch

    patch()
    monkeypatch.setattr(ddtrace._monkey, "_PATCHED_MODULES", ddtrace._monkey._PATCHED_MODULES | {"typesafe"})
    yield
    unpatch()


@pytest.fixture
def openai_enabled(openai_patched, monkeypatch):
    import ddtrace._monkey

    monkeypatch.setattr(ddtrace._monkey, "_PATCHED_MODULES", ddtrace._monkey._PATCHED_MODULES | {"openai"})
    yield


@pytest.mark.parametrize("ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>")])
class TestPydanticAIDecisions:
    async def test_typesafe_workflow_over_provider_span(
        self, pydantic_ai, pydantic_ai_llmobs, typesafe_patched, test_spans
    ):
        result = await run_triage(typesafe_model())
        assert result.output.team == "engineering"
        spans = _spans(test_spans)
        (workflow,) = _decision_spans(spans)
        (provider,) = [s for s in spans if s.name == "typesafe.request"]
        assert provider.parent_id == workflow.span_id
        assert_llmobs_span_data(_llmobs(workflow), span_kind="workflow", name="TypeSafeModel.decide")
        output = json.loads(_llmobs(workflow)["meta"]["output"]["value"])
        assert output["answers"]["team"]["choice"] == "engineering"
        assert not _llmobs(workflow).get("metrics")
        assert "decision_provider" not in (_llmobs(workflow).get("tags") or {})
        provider_metrics = _llmobs(provider)["metrics"]
        assert provider_metrics["total_tokens"] > 0
        assert _total_tokens(spans) == provider_metrics["total_tokens"]

    async def test_typesafe_llm_without_provider_integration(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        await run_triage(typesafe_model())
        spans = _spans(test_spans)
        assert not [s for s in spans if s.name == "typesafe.request"]
        (span,) = _decision_spans(spans)
        data = _llmobs(span)
        assert_llmobs_span_data(
            data,
            span_kind="llm",
            name="TypeSafeModel.decide",
            model_provider="typesafe",
            tags={"decision_provider": "typesafe"},
        )
        assert data["metrics"]["total_tokens"] == data["metrics"]["input_tokens"] + data["metrics"]["output_tokens"] > 0

    async def test_system_one(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        result = await run_triage(system_one_model())
        assert result.output.team == "engineering"
        (span,) = _decision_spans(_spans(test_spans))
        data = _llmobs(span)
        assert_llmobs_span_data(
            data,
            span_kind="llm",
            name="SystemOneModel.decide",
            model_provider="typesafe",
            tags={"decision_provider": "typesafe"},
        )
        output = json.loads(data["meta"]["output"]["messages"][0]["content"])
        assert output["answers"]["urgent"]["type"] == "noul"
        assert data["metrics"]["total_tokens"] == data["metrics"]["input_tokens"] + data["metrics"]["output_tokens"] > 0

    async def test_openai_workflow_over_provider_span(
        self, pydantic_ai, pydantic_ai_llmobs, openai_enabled, test_spans
    ):
        result = await run_triage(openai_model())
        assert result.output.team == "engineering"
        spans = _spans(test_spans)
        (workflow,) = _decision_spans(spans)
        (provider,) = [s for s in spans if s.name == "openai.request"]
        assert provider.parent_id == workflow.span_id
        assert provider.resource == "createDecision"
        assert_llmobs_span_data(_llmobs(workflow), span_kind="workflow", name="OpenAIDecisionsModel.decide")
        assert_llmobs_span_data(_llmobs(provider), span_kind="llm", tags={"decision_provider": "openai"})
        assert _total_tokens(spans) == _llmobs(provider)["metrics"]["total_tokens"] > 0

    async def test_openai_llm_without_provider_integration(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        await run_triage(openai_model())
        (span,) = _decision_spans(_spans(test_spans))
        data = _llmobs(span)
        assert_llmobs_span_data(
            data,
            span_kind="llm",
            name="OpenAIDecisionsModel.decide",
            model_provider="openai",
            tags={"decision_provider": "openai"},
        )
        answers = json.loads(data["meta"]["output"]["messages"][0]["content"])["answers"]
        assert answers == _recorded_openai_answers()
        TypeAdapter(list[Answer]).validate_python(answers)
        assert data["metrics"] == {"input_tokens": 330, "output_tokens": 0, "total_tokens": 330}

    async def test_openai_llm_answer_shapes(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai.models.openai_decisions import OpenAIDecisionsModel

        class LocalOpenAIDecisionsModel(OpenAIDecisionsModel):
            async def decide(self, request, model_settings):
                return decision.DecisionResponse(
                    model_name="gpt-6-luna",
                    answers={
                        "urgent": decision.NoulAnswer(noul=0.98),
                        "team": decision.ChoiceAnswer(
                            choice="engineering", confidence=0.9, probabilities={"billing": 0.1, "engineering": 0.9}
                        ),
                        "severity": decision.ScoreAnswer(
                            score=1.8,
                            confidence=0.7,
                            probabilities={0: 0.0, 1: 0.2, 2: 0.8},
                            legend={0: "low", 1: "medium", 2: "high"},
                        ),
                    },
                )

        model = LocalOpenAIDecisionsModel("gpt-6-luna", provider=openai_model()._provider)
        async with model._decide(decision.DecisionRequest(state=TICKET, questions={}), {}) as _:
            pass
        (span,) = _decision_spans(_spans(test_spans))
        answers = json.loads(_llmobs(span)["meta"]["output"]["messages"][0]["content"])["answers"]
        assert answers == [
            {"type": "predicate", "name": "urgent", "probability": 0.98},
            {
                "type": "choice",
                "name": "team",
                "choice": "engineering",
                "probabilities": [
                    {"value": "billing", "probability": 0.1},
                    {"value": "engineering", "probability": 0.9},
                ],
                "confidence": 0.9,
            },
            {
                "type": "score",
                "name": "severity",
                "score": 1.8,
                "probabilities": [
                    {"value": 0, "label": "low", "probability": 0.0},
                    {"value": 1, "label": "medium", "probability": 0.2},
                    {"value": 2, "label": "high", "probability": 0.8},
                ],
                "confidence": 0.7,
            },
        ]
        TypeAdapter(list[Answer]).validate_python(answers)

    async def test_google_error(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai.exceptions import ModelHTTPError

        with pytest.raises(ModelHTTPError):
            await run_triage(google_model())
        (span,) = _decision_spans(_spans(test_spans))
        assert span.error == 1
        data = _llmobs(span)
        assert_llmobs_span_data(
            data,
            span_kind="llm",
            name="GoogleDecisionsModel.decide",
            model_provider="google",
            tags={"decision_provider": "google"},
            error=pytest.importorskip("unittest.mock").ANY,
        )
        assert data["meta"]["output"]["messages"][0]["content"] == ""

    async def test_local_backend_llm_span(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        model = LocalDecisionModel()
        await run_triage(model)
        (span,) = _decision_spans(_spans(test_spans))
        data = _llmobs(span)
        input_body = json.loads(data["meta"]["input"]["messages"][0]["content"])
        assert input_body["state"] == model.requests[0].state
        assert input_body["questions"]["urgent"]["type"] == "noul"
        assert json.loads(data["meta"]["output"]["messages"][0]["content"])["answers"]["urgent"] == {
            "type": "noul",
            "noul": 0.9,
        }
        assert_llmobs_span_data(
            data,
            span_kind="llm",
            name="LocalDecisionModel.decide",
            model_name="local-decider-1",
            model_provider="local",
            metrics={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        )
        assert "decision_provider" not in (data.get("tags") or {})

    async def test_decide_error(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai.exceptions import ModelHTTPError

        with pytest.raises(ModelHTTPError):
            await run_triage(LocalDecisionModel(fail=True))
        (span,) = _decision_spans(_spans(test_spans))
        assert span.error == 1
        assert _llmobs(span)["meta"]["output"]["messages"][0]["content"] == ""
        assert not _llmobs(span).get("metrics")

    async def test_run_stream(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai import Agent

        async with Agent(LocalDecisionModel(), output_type=Triage).run_stream(TICKET) as result:
            await result.get_output()
        (span,) = _decision_spans(_spans(test_spans))
        assert_llmobs_span_data(_llmobs(span), span_kind="llm", model_name="local-decider-1")

    async def test_route_pick_and_fill(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        from pydantic_ai import Agent

        def lookup_account(account_id: str) -> str:
            """Look up an account."""
            return "ok"

        model = LocalDecisionModel(max_questions=2)
        await Agent(model, output_type=Triage, tools=[lookup_account]).run(TICKET)
        spans = _spans(test_spans)
        decisions = _decision_spans(spans)
        assert len(model.requests) == 2
        assert len(decisions) == 2
        assert decisions[0].parent_id == decisions[1].parent_id
        assert all(_llmobs(s)["metrics"]["total_tokens"] == 14 for s in decisions)


@pytest.mark.snapshot(ignores=["meta._dd.p.llmobs_trace_id", "meta._dd.p.tid", "metrics.process_id"])
async def test_system_one_snapshot(pydantic_ai):
    await run_triage(system_one_model())
