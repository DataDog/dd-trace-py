from abc import abstractmethod
from typing import Dict
from typing import Iterable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.utils import LLMObsSpanContext

from ._client import LLMObsSpansAPI


log = get_logger(__name__)


MultipleEvaluations = Dict[str, Union[str, float, int]]


class Evaluation(Protocol):
    @property
    def label(self):
        return self.__class__.__name__.lower()

    def __call__(self, span: LLMObsSpanContext, **kwargs) -> Optional[Union[str, float, int, MultipleEvaluations]]:
        return self.run(span, **kwargs)

    @abstractmethod
    def run(self, span: LLMObsSpanContext, **kwargs) -> Optional[Union[str, float, int, MultipleEvaluations]]:
        pass


class EvaluationFilter:
    # this class defines an evp filter to run the evals on
    pass


class _EvaluationRunner:
    def __init__(self):
        self.client = LLMObsSpansAPI()
        self.filter_query = ""
        self.filter_from = ""
        self.filter_to = ""
        self.evaluations = []

    def _download_spans(self) -> Iterable[LLMObsSpanContext]:
        for batch_of_raw_spans in self.client.search_spans(
            filter_query="@event_type:span {}".format(self.filter_query).strip(),
            filter_from=self.filter_from,
            filter_to=self.filter_to,
        ):
            for raw_span in batch_of_raw_spans:
                log.debug("Downloaded 10 LLM Obs spans from spans api")
                try:
                    yield LLMObsSpanContext.model_validate(raw_span)
                except Exception:
                    log.warning("failed to validate span data from spans api")

    def configure(self, evaluations=None, filter_query="", start="", stop=""):
        if filter_query:
            self.filter_query = filter_query
        if start:
            self.filter_from = start
        if stop:
            self.filter_to = stop
        if evaluations:
            self.evaluations = evaluations
        return self

    def start(self, evaluations: Iterable[Evaluation] = None, filter_query="", filter_from="", filter_to=""):
        self.configure(evaluations=evaluations, filter_query=filter_query, start=filter_from, stop=filter_to)

        if len(self.evaluations) == 0:
            log.warning("No specificied evaluations to run")
            return

        for span in self._download_spans():
            for evaluation in self.evaluations:
                if hasattr(evaluation, "label"):
                    label = evaluation.label
                else:
                    label = evaluation.__name__

                try:
                    result = evaluation(span=span)
                except TypeError as e:
                    log.error("failed to run evaluation `%s` on span:", label, e)
                    continue

                if result is None:
                    log.debug("evaluation returned None, not submitting evaluation")
                    continue

                def submit_eval(span, label, value):
                    if isinstance(value, bool):
                        value = str(value)

                    join = {"trace_id": span.trace_id, "span_id": span.span_id}

                    tags = {}

                    try:
                        for tag in span.tags:
                            k, v = tag.split(":")
                            tags[k] = v
                    except Exception as e:
                        log.error("failed to parse tags: ", e)
                        return

                    if isinstance(value, (int, float)):
                        LLMObs.submit_evaluation(
                            span_context=join, label=label, metric_type="score", value=value, tags=tags
                        )
                    else:
                        LLMObs.submit_evaluation(
                            span_context=join, label=label, metric_type="categorical", value=str(value), tags=tags
                        )

                if isinstance(result, Dict):
                    for key, value in result.items():
                        submit_eval(span, key, value)
                else:
                    submit_eval(span, label, result)


EvaluationRunner = _EvaluationRunner()


def run_evaluations(
    evaluations: Iterable[Evaluation],
    spans: Iterable[LLMObsSpanContext],
    **kwargs,
):
    for runner in evaluations:
        for span in spans:
            if span is None:
                log.warning("detected Null span in run_evaluations")
                continue
            if not isinstance(span, LLMObsSpanContext):
                log.warning("skipping span, span is not type LLMObsSpanContext")
                continue

            result = runner(span=span, **kwargs)

            if result is None:
                log.debug("evaluation returned None, not submitting evaluation")
                continue

            def submit_eval(span, label, value):
                if isinstance(value, bool):
                    value = str(value)

                join = {"trace_id": span.trace_id, "span_id": span.span_id}

                if isinstance(value, (int, float)):
                    LLMObs.submit_evaluation(
                        span_context=join, label=label, metric_type="score", value=value, tags=span.tags
                    )
                else:
                    LLMObs.submit_evaluation(
                        span_context=join, label=label, metric_type="categorical", value=str(value), tags=span.tags
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
