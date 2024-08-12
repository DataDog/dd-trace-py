from abc import abstractmethod
import math
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.evaluations import Evaluation
from ddtrace.llmobs.utils import LLMObsSpanContext


log = get_logger(__name__)

try:
    from datasets import Dataset
    from ragas import evaluate as ragas_evaluate
except Exception:
    log.warning("Failed to import required libraries for Ragas evaluations.")


class RagasBase(Evaluation):
    @property
    def label(self):
        return "ragas.{}".format(self.name)

    @property
    @abstractmethod
    def name(self):
        pass

    @property
    @abstractmethod
    def ragas_metric(self):
        pass

    @abstractmethod
    def translate_input(self, span: LLMObsSpanContext) -> Dataset:
        pass

    def run(self, span: LLMObsSpanContext, **kwargs) -> Optional[float]:
        ragas_input = self.translate_input(span)

        if ragas_input is None:
            return None

        result = ragas_evaluate(metrics=[self.ragas_metric], dataset=ragas_input)[self.name]
        if not math.isnan(result):
            return result
        return None
