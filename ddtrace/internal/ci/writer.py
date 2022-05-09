import os
from typing import Dict
from typing import Optional
from typing import TYPE_CHECKING

from ddtrace.internal.logger import get_logger
from ddtrace.internal.writer import HTTPWriter

from .encoding import AgentlessEncoderV1


if TYPE_CHECKING:
    from ddtrace.sampler import BasePrioritySampler
    from ddtrace.sampler import BaseSampler
    from ddtrace.vendor.dogstatsd import DogStatsd


log = get_logger(__name__)

DEFAULT_TIMEOUT = 1.0


def get_ci_writer_timeout():
    # type: () -> float
    return float(os.getenv("DD_CI_WRITER_TIMEOUT_SECONDS", default=DEFAULT_TIMEOUT))


class AgentlessWriter(HTTPWriter):
    """Writer to the CI Visibility intake endpoint."""

    RETRY_ATTEMPTS = 5

    REQUEST_HTTP_METHOD = "POST"

    ENDPOINT_TEMPLATE = "https://citestcycle-intake.{}"

    def __init__(
        self,
        intake_url=None,  # type: Optional[str]
        sampler=None,  # type: Optional[BaseSampler]
        priority_sampler=None,  # type: Optional[BasePrioritySampler]
        processing_interval=5.0,  # type: float
        # Match the payload size since there is no functionality
        # to flush dynamically.
        buffer_size=None,  # type: Optional[int]
        max_payload_size=None,  # type: Optional[int]
        timeout=get_ci_writer_timeout(),  # type: float
        dogstatsd=None,  # type: Optional[DogStatsd]
        report_metrics=False,  # type: bool
        sync_mode=True,  # type: bool
        reuse_connections=None,  # type: Optional[bool]
        headers=None,  # type: Optional[Dict[str, str]]
    ):
        # type: (...) -> None
        if headers is None:
            headers = {}

        if "dd-api-key" not in headers:
            api_key = os.environ.get("DATADOG_API_KEY", os.environ.get("DD_API_KEY"))  # type: Optional[str]
            if api_key is None:
                raise ValueError("DATADOG_API_KEY or DD_API_KEY must be set")
            headers["dd-api-key"] = api_key

        if intake_url is None:
            intake_url = self.ENDPOINT_TEMPLATE.format(os.environ.get("DD_SITE", "datadoghq.com"))

        super(AgentlessWriter, self).__init__(
            intake_url=intake_url,
            endpoint="/api/v2/citestcycle",
            encoder_cls=AgentlessEncoderV1,
            sampler=sampler,
            priority_sampler=priority_sampler,
            processing_interval=processing_interval,
            buffer_size=buffer_size,
            max_payload_size=max_payload_size,
            timeout=timeout,
            dogstatsd=dogstatsd,
            report_metrics=report_metrics,
            sync_mode=sync_mode,
            reuse_connections=reuse_connections,
            headers=headers,
        )

    def recreate(self):
        # type: () -> AgentlessWriter
        return self.__class__(
            intake_url=self.intake_url,
            sampler=self._sampler,
            priority_sampler=self._priority_sampler,
            processing_interval=self._interval,
            buffer_size=self._buffer_size,
            max_payload_size=self._max_payload_size,
            timeout=self._timeout,
            dogstatsd=self.dogstatsd,
            report_metrics=self._report_metrics,
            sync_mode=self._sync_mode,
        )
