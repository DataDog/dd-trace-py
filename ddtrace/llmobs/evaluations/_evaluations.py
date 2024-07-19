from abc import abstractmethod
from typing import Dict
from typing import Iterable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.utils import ExportedLLMObsSpan


MultipleEvaluations = Dict[str, Union[str, float, int]]


class Evaluation(Protocol):
    @property
    def label(self):
        return self.__class__.__name__.lower()

    def __call__(self, span: ExportedLLMObsSpan, **kwargs) -> Optional[Union[str, float, int, MultipleEvaluations]]:
        return self.run(span, **kwargs)

    @abstractmethod
    def run(self, span: ExportedLLMObsSpan, **kwargs) -> Optional[Union[str, float, int, MultipleEvaluations]]:
        pass


def run_evaluations(
    evaluations: Iterable[Evaluation],
    spans: Iterable[ExportedLLMObsSpan],
):
    for runner in evaluations:
        for span in spans:
            result = runner(span=span)
            print("result: {}".format(result))

            def submit_eval(span, label, value):
                if isinstance(value, bool):
                    value = str(value)
                if isinstance(value, (int, float)):
                    LLMObs.submit_evaluation(span_context=span, label=label, metric_type="score", value=value)
                else:
                    LLMObs.submit_evaluation(
                        span_context=span, label=label, metric_type="categorical", value=str(value)
                    )

            if isinstance(result, Dict):
                for key, value in result.items():
                    submit_eval(span, key, value)
            else:
                label = None
                if hasattr(runner, "label"):
                    label = runner.label
                else:
                    label = runner.__class__.__name__
                submit_eval(span, label, result)
