from abc import abstractmethod
from typing import Optional

from ddtrace.llmobs.evaluations import Evaluation
from ddtrace.llmobs.utils import ExportedLLMObsSpan


try:
    from datasets import Dataset
    from ragas import evaluate as ragas_evaluate
except Exception:
    print("Ragas not installed")


class RagasBase(Evaluation):
    @property
    def label(self):
        return "ragas.{}".format(self.name)

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def translate_input(self, span: ExportedLLMObsSpan) -> Dataset:
        pass

    def run(self, span: ExportedLLMObsSpan, **kwargs) -> Optional[float]:
        ragas_input = self.translate_input(span)

        if ragas_input is None:
            return None

        result = ragas_evaluate(ragas_input)

        return result[self.name]
