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
from .._encoding import BufferedEncoder
from ..runtime import get_runtime_id
from ..writer import HTTPWriter
from ..writer import TraceWriter
from ..writer import get_writer_interval_seconds
from .encoder import CIVisibilityCoverageEncoderV02
from .encoder import CIVisibilityEncoderV01


class CIVisibilityClientBase:
    def __init__(
        self,
        encoder,  # type: BufferedEncoder
    ):
        self.encoder = encoder


class CIVisibilityEventClient(CIVisibilityClientBase):
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


class CIVisibilityCoverageClient(CIVisibilityClientBase):
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
        intake_url,  # type: str
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
        self._clients = [CIVisibilityEventClient(), CIVisibilityCoverageClient()]

    def set_encoder(self, encoder):
        for client in self._clients:
            client.encoder = encoder

    def _put_encoder(self, spans):
        for client in self._clients:
            client.encoder.put(spans)

    def flush_queue(self, raise_exc=False):
        try:
            for client in self._clients:
                self._flush_queue_with_client(client=client, raise_exc=raise_exc)
        finally:
            self._set_drop_rate()
            self._metrics_reset()

    def write(self, spans=None):
        for client in self._clients:
            self._write_with_client(client=client, spans=spans)

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
            api_version=self._api_version,
        )
