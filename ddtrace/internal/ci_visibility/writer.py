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
from ..writer import get_writer_interval_seconds
from .constants import AGENTLESS_ENDPOINT
from .constants import EVP_PROXY_AGENT_BASE_PATH
from .constants import EVP_PROXY_AGENT_ENDPOINT
from .constants import EVP_SUBDOMAIN_HEADER_NAME
from .constants import EVP_SUBDOMAIN_HEADER_VALUE
from .encoder import CIVisibilityEncoderV01


class CIVisibilityWriter(HTTPWriter):
    RETRY_ATTEMPTS = 5
    HTTP_METHOD = "PUT"
    STATSD_NAMESPACE = "civisibilitywriter"
    STATE_AGENTLESS = 0
    STATE_AGENTPROXY = 1

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
        encoder = CIVisibilityEncoderV01(0, 0)
        encoder.set_metadata(
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

        self._state = self.STATE_AGENTLESS

        super(CIVisibilityWriter, self).__init__(
            intake_url=intake_url,
            endpoint=AGENTLESS_ENDPOINT,
            encoder=encoder,
            sampler=sampler,
            priority_sampler=priority_sampler,
            processing_interval=processing_interval,
            timeout=timeout,
            dogstatsd=dogstatsd,
            sync_mode=sync_mode,
            reuse_connections=reuse_connections,
            headers=headers,
        )
        self._set_state(self._check_agent())

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

    def _check_agent(self):
        try:
            info = agent.info()
        except Exception:
            info = None

        if info:
            endpoints = info.get("endpoints", [])
            if endpoints and (EVP_PROXY_AGENT_BASE_PATH in endpoints or ("/" + EVP_PROXY_AGENT_BASE_PATH) in endpoints):
                return self.STATE_AGENTPROXY
        return self._state

    def _set_state(self, new_state):
        if new_state == self.STATE_AGENTPROXY:
            self._state = new_state
            self._endpoint = EVP_PROXY_AGENT_ENDPOINT
            self.intake_url = agent.get_trace_url()
            self._headers[EVP_SUBDOMAIN_HEADER_NAME] = EVP_SUBDOMAIN_HEADER_VALUE
