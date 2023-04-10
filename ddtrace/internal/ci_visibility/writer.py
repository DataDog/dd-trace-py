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
from ..writer import TraceWriter
from ..writer import get_writer_interval_seconds
from .encoder import CIVisibilityCoverageEncoderV02
from .encoder import CIVisibilityEncoderV01


class CIVisibilityWriter(HTTPWriter):
    RETRY_ATTEMPTS = 5
    HTTP_METHOD = "PUT"
    STATSD_NAMESPACE = "civisibility.writer"

    def __init__(
        self,
        intake_url=None,  # type: Optional[str]
        sampler=None,  # type: Optional[BaseSampler]
        priority_sampler=None,  # type: Optional[BasePrioritySampler]
        processing_interval=get_writer_interval_seconds(),  # type: float
        timeout=agent.get_trace_agent_timeout(),  # type: float
        dogstatsd=None,  # type: Optional[DogStatsd]
        sync_mode=True,  # type: bool
        report_metrics=False,  # type: bool
        api_version=None,  # type: Optional[str]
        reuse_connections=None,  # type: Optional[bool]
        headers=None,  # type: Optional[Dict[str, str]]
    ):
        if not intake_url:
            intake_url = "https://citestcycle-intake.%s" % os.environ.get("DD_SITE", "datadoghq.com")
        event_encoder = CIVisibilityEncoderV01(0, 0)
        event_encoder.set_metadata(
            {
                "language": "python",
                "env": config.env,
                "runtime-id": get_runtime_id(),
                "library_version": ddtrace.__version__,
            }
        )
        headers = headers or dict()
        headers["dd-api-key"] = os.environ.get("DD_API_KEY") or ""
        if not headers["dd-api-key"]:
            raise ValueError("Required environment variable DD_API_KEY not defined")

        super(CIVisibilityWriter, self).__init__(
            intake_url=intake_url,
            endpoint="api/v2/citestcycle",
            encoder=event_encoder,
            sampler=sampler,
            priority_sampler=priority_sampler,
            processing_interval=processing_interval,
            timeout=timeout,
            dogstatsd=dogstatsd,
            sync_mode=sync_mode,
            reuse_connections=reuse_connections,
            headers=headers,
        )

    def stop(self, timeout=None):
        if self.status != service.ServiceStatus.STOPPED:
            super(CIVisibilityWriter, self).stop(timeout=timeout)

    def recreate(self):
        # type: () -> HTTPWriter
        return self.__class__(
            intake_url=self.intake_url,
            sampler=self._sampler,
            priority_sampler=self._priority_sampler,
            processing_interval=self._interval,
            timeout=self._timeout,
            dogstatsd=self.dogstatsd,
            sync_mode=self._sync_mode,
        )


class CIVisibilityEventWriter(CIVisibilityWriter):
    STATSD_NAMESPACE = "civisibility.eventwriter"

    def __init__(self, **kwargs):
        event_encoder = CIVisibilityEncoderV01(0, 0)
        event_encoder.set_metadata(
            {
                "language": "python",
                "env": config.env,
                "runtime-id": get_runtime_id(),
                "library_version": ddtrace.__version__,
            }
        )

        super(CIVisibilityWriter, self).__init__(endpoint="api/v2/citestcycle", encoder=event_encoder, **kwargs)


class CIVisibilityCoverageWriter(CIVisibilityWriter):
    STATSD_NAMESPACE = "civisibility.coveragewriter"

    def __init__(self, **kwargs):
        super(CIVisibilityWriter, self).__init__(
            endpoint="api/v2/citestcov", encoder=CIVisibilityCoverageEncoderV02(0, 0), **kwargs
        )


class CIVisibilityWriterGroup(TraceWriter):
    def __init__(self, **kwargs):
        _event_writer = CIVisibilityEventWriter(**kwargs)
        _coverage_writer = CIVisibilityCoverageWriter(**kwargs)
        self._writers = [_event_writer, _coverage_writer]

    def stop(self, timeout=None):
        for writer in self._writers:
            writer.stop(timeout=timeout)

    def flush_queue(self, raise_exc=False):
        for writer in self._writers:
            writer.flush_queue(raise_exc=raise_exc)

    def write(self, spans=None):
        for writer in self._writers:
            writer.write(spans=spans)
