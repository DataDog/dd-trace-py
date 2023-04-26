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
from .constants import AGENTLESS_BASE_URL
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import AGENTLESS_ENDPOINT
from .constants import EVP_PROXY_AGENT_ENDPOINT
from .encoder import CIVisibilityEncoderV01


class CIVisibilityEventClient(WriterClientBase):
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


class CIVisibilityAgentlessEventClient(CIVisibilityEventClient):
    ENDPOINT = AGENTLESS_ENDPOINT


class CIVisibilityProxiedEventClient(CIVisibilityEventClient):
    ENDPOINT = EVP_PROXY_AGENT_ENDPOINT


class CIVisibilityWriter(HTTPWriter):
    RETRY_ATTEMPTS = 5
    HTTP_METHOD = "POST"
    STATSD_NAMESPACE = "civisibilitywriter"

    def __init__(
        self,
        intake_url="",  # type: str
        sampler=None,  # type: Optional[BaseSampler]
        priority_sampler=None,  # type: Optional[BasePrioritySampler]
        processing_interval=get_writer_interval_seconds(),  # type: float
        timeout=agent.get_trace_agent_timeout(),  # type: float
        dogstatsd=None,  # type: Optional[DogStatsd]
        sync_mode=False,  # type: bool
        report_metrics=False,  # type: bool
        api_version=None,  # type: Optional[str]
        reuse_connections=None,  # type: Optional[bool]
        headers=None,  # type: Optional[Dict[str, str]]
        use_evp=False,  # type: bool
    ):
        if config._ci_visibility_agentless_url:
            intake_url = config._ci_visibility_agentless_url
        if not intake_url:
            intake_url = "%s.%s" % (AGENTLESS_BASE_URL, os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE))

        client = CIVisibilityProxiedEventClient() if use_evp else CIVisibilityAgentlessEventClient()

        headers = headers or dict()
        headers.update({"Content-Type": client.encoder.content_type})  # type: ignore[attr-defined]

        super(CIVisibilityWriter, self).__init__(
            intake_url=intake_url,
            clients=[client],
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
