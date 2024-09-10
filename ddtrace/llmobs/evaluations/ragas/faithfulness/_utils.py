import typing as t

from langchain_core.pydantic_v1 import BaseModel
from langchain_core.pydantic_v1 import Field
from ragas.llms.output_parser import RagasoutputParser
from ragas.llms.prompt import Prompt


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


context_parser = RagasoutputParser(pydantic_object=ExtractedContext)


extract_inputs_from_messages_prompt = Prompt(
    name="extract_context",
    instruction="""You will be given a prompt to a large language model.
                    The prompt will contain a question and the reference information
                    that should be used to reference that question.
                    Your task is to extract out the reference information.
                    Do not include any text that is not in the original input.""",
    examples=[
        {
            "messages": [
                {
                    "role": "user",
                    "content": """
Given the following question and reference context, answer the user's question
question: What are the effects of carbonated water on teeth?
context_str: Carbonated water has negative, destructive effects on teeth, and result in
decreasing microhardness and removal of the adhesive material on etched or sealed enamel.
Erosion occurred when the etched enamel of teeth was exposed to carbonated water,
particularly in groups exposed to high-level carbonated water.
Alleviation of this destructive effect is observed in groups exposed to carbonated water with calcium ion.
Partial removal of the adhesive material on sealed enamel could be observed after exposure to carbonated water.
                """,
                },
            ],
            "output": {
                "context": """
Carbonated water has negative, destructive effects on teeth, and result in
decreasing microhardness and removal of the adhesive material on etched or sealed enamel.
Erosion occurred when the etched enamel of teeth was exposed to carbonated water,
particularly in groups exposed to high-level carbonated water.
Alleviation of this destructive effect is observed in groups exposed to carbonated water with calcium ion.
Partial removal of the adhesive material on sealed enamel could be observed after exposure to carbonated water.
                """,
                "question": "What are the effects of carbonated water on teeth?",
            },
        },
    ],
    input_keys=["messages"],
    output_key="output",
    output_format_instruction="""
    The output should be a json with the question and the context extracted from the messages.
    For example:
    {
        "context": "Extracted context",
        "question": "Extracted question"
    }
    """,
    output_type="json",
)
