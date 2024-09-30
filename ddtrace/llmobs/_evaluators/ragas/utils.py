import typing as t

from langchain_core.pydantic_v1 import BaseModel
from langchain_core.pydantic_v1 import Field

from ddtrace.internal.utils.formats import parse_tags_str


def _get_ml_app_for_ragas_trace(span_event: dict) -> str:
    """
    The `ml_app` spans generated from traces of ragas will be named as `ragas-<ml_app>`
    or `ragas` if `ml_app` is not present in the span tags.
    """
    ml_app_of_span_event = ""
    span_tags = span_event.get("tags")
    if span_tags is not None:
        ml_app_of_span_event = "-{}".format(parse_tags_str(",".join(span_tags)).get("ml_app"))
    return "ragas{}".format(ml_app_of_span_event)


class StatementFaithfulnessAnswer(BaseModel):
    statement: str = Field(..., description="the original statement, word-by-word")
    reason: str = Field(..., description="the reason of the verdict")
    verdict: int = Field(..., description="the verdict(0/1) of the faithfulness.")


class StatementFaithfulnessAnswers(BaseModel):
    __root__: t.List[StatementFaithfulnessAnswer]

    def dicts(self) -> t.List[t.Dict]:
        return self.dict()["__root__"]


class Statements(BaseModel):
    sentence_index: int = Field(..., description="Index of the sentence from the statement list")
    simpler_statements: t.List[str] = Field(..., description="the simpler statements")


class StatementsAnswers(BaseModel):
    __root__: t.List[Statements]

    def dicts(self) -> t.List[t.Dict]:
        return self.dict()["__root__"]


class ExtractedContext(BaseModel):
    context: str = Field(..., description="the extracted context")
    question: str = Field(..., description="the extracted question")
