from ddtrace.llmobs.utils import ExportedLLMObsSpan

from ._base import RagasBase


try:
    from datasets import Dataset
except Exception:
    pass


class RagasFaithfulness(RagasBase):
    @property
    def name(self):
        "faithfulness"

    @property
    def type(self):
        return "score"

    def translate_input(self, span: ExportedLLMObsSpan):
        documents = []
        answer = None

        if len(span["meta"]["input"]["documents"]) > 0:
            documents = span["meta"]["input"]["documents"]
        elif len(span["meta"]["output"]["documents"]) > 0:
            documents = span["meta"]["output"]["documents"]

        if len(documents) == 0:
            return None

        answer = span["meta"]["output"]["value"]
        if not answer:
            answer = span["meta"]["output"]["messages"][0]["content"]

        if answer is None:
            return None

        return Dataset.from_dict({"answer": answer, "ground_truths": documents})
