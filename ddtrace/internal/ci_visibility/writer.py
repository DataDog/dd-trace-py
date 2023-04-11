import os
from typing import Dict
from typing import Optional

import ddtrace
from ddtrace import config
from ddtrace.vendor.dogstatsd import DogStatsd

from .. import agent
from .. import service
from ...sampler import BasePrioritySampler
from ...sampler import BaseSampler
from ..runtime import get_runtime_id
from ..writer import HTTPWriter
from ..writer import WriterClientBase
from ..writer import get_writer_interval_seconds
from .encoder import CIVisibilityCoverageEncoderV02
from .encoder import CIVisibilityEncoderV01


class CIVisibilityEventClient(WriterClientBase):
    ENDPOINT = "api/v2/citestcycle"

    def __init__(self):
        encoder = CIVisibilityEncoderV01(0, 0)
        encoder.set_metadata(
            {
                "language": "python",
                "env": config.env,
                "runtime-id": get_runtime_id(),
                "library_version": ddtrace.__version__,
            }
        )
        super(CIVisibilityEventClient, self).__init__(encoder)


class CIVisibilityCoverageClient(WriterClientBase):
    ENDPOINT = "api/v2/citestcov"

    def __init__(self):
        encoder = CIVisibilityCoverageEncoderV02(0, 0)
        super(CIVisibilityCoverageClient, self).__init__(encoder)


class CIVisibilityWriter(HTTPWriter):
    RETRY_ATTEMPTS = 5
    HTTP_METHOD = "PUT"
    STATSD_NAMESPACE = "civisibility.writer"

    def __init__(
        self,
        intake_url="",  # type: str
        sampler=None,  # type: Optional[BaseSampler]
        priority_sampler=None,  # type: Optional[BasePrioritySampler]
        processing_interval=get_writer_interval_seconds(),  # type: float
        buffer_size=None,  # type: Optional[int]
        max_payload_size=None,  # type: Optional[int]
        timeout=agent.get_trace_agent_timeout(),  # type: float
        dogstatsd=None,  # type: Optional[DogStatsd]
        report_metrics=False,  # type: bool
        sync_mode=False,  # type: bool
        api_version=None,  # type: Optional[str]
        reuse_connections=None,  # type: Optional[bool]
        headers=None,  # type: Optional[Dict[str, str]]
    ):
        if not intake_url:
            intake_url = "https://citestcycle-intake.%s" % os.environ.get("DD_SITE", "datadoghq.com")
        headers = headers or dict()
        headers["dd-api-key"] = os.environ.get("DD_API_KEY") or ""
        if not headers["dd-api-key"]:
            raise ValueError("Required environment variable DD_API_KEY not defined")
        super(CIVisibilityWriter, self).__init__(
            intake_url=intake_url,
            clients=[CIVisibilityEventClient(), CIVisibilityCoverageClient()],
            sampler=sampler,
            priority_sampler=priority_sampler,
            processing_interval=processing_interval,
            buffer_size=buffer_size,
            max_payload_size=max_payload_size,
            timeout=timeout,
            dogstatsd=dogstatsd,
            sync_mode=sync_mode,
            reuse_connections=reuse_connections,
            headers=headers,
        )

    @property
    def _intake_endpoint(self):
        return "{}/{}".format(self.intake_url, self._clients[0].ENDPOINT)

    def stop(self, timeout=None):
        if self.status != service.ServiceStatus.STOPPED:
            super(CIVisibilityWriter, self).stop(timeout=timeout)

    def recreate(self):
        # type: () -> HTTPWriter
        return self.__class__(
            self.intake_url,
            sampler=self._sampler,
            priority_sampler=self._priority_sampler,
            processing_interval=self._interval,
            buffer_size=self._buffer_size,
            max_payload_size=self._max_payload_size,
            timeout=self._timeout,
            dogstatsd=self.dogstatsd,
            sync_mode=self._sync_mode,
        )
