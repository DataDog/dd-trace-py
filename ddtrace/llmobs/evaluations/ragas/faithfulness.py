from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.utils import LLMObsSpanContext

from ._base import RagasBase


try:
    from datasets import Dataset
    from ragas.metrics import faithfulness
except Exception:
    pass

log = get_logger(__name__)


class RagasFaithfulness(RagasBase):
    @property
    def name(self):
        return "faithfulness"

    @property
    def ragas_metric(self):
        return faithfulness

    @property
    def type(self):
        return "score"

    def translate_input(self, span: LLMObsSpanContext):
        try:
            question = span.meta.input.messages[-1]["content"]
            answer = span.meta.output.messages[0]["content"]
            context = span.meta.metadata["prompt_template"]["variables"]["context"]
        except Exception as e:
            log.warning("Error translating input to RAGAS format: ", e)
        return Dataset.from_dict({"question": [question], "answer": [answer], "contexts": [[context]]})
