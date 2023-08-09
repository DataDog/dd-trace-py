import os
from typing import TYPE_CHECKING

import ddtrace
from ddtrace import config

from .. import agent
from .. import service
from ..runtime import get_runtime_id
from ..writer import HTTPWriter
from ..writer import WriterClientBase
from ..writer import get_writer_interval_seconds
from .constants import AGENTLESS_BASE_URL
from .constants import AGENTLESS_COVERAGE_BASE_URL
from .constants import AGENTLESS_COVERAGE_ENDPOINT
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import AGENTLESS_ENDPOINT
from .constants import EVP_PROXY_AGENT_ENDPOINT
from .constants import EVP_PROXY_COVERAGE_ENDPOINT
from .constants import EVP_SUBDOMAIN_HEADER_COVERAGE_VALUE
from .constants import EVP_SUBDOMAIN_HEADER_NAME
from .encoder import CIVisibilityCoverageEncoderV02
from .encoder import CIVisibilityEncoderV01


if TYPE_CHECKING:  # pragma: no cover
    from typing import Dict
    from typing import List
    from typing import Optional

    from ddtrace.vendor.dogstatsd import DogStatsd

    from ...sampler import BasePrioritySampler
    from ...sampler import BaseSampler


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


class CIVisibilityCoverageClient(WriterClientBase):
    def __init__(self, intake_url, headers=None, itr_suite_skipping_mode=False):
        encoder = CIVisibilityCoverageEncoderV02(0, 0)
        if itr_suite_skipping_mode:
            encoder._set_itr_suite_skipping_mode(itr_suite_skipping_mode)
        self._intake_url = intake_url
        if headers:
            self._headers = headers
        super(CIVisibilityCoverageClient, self).__init__(encoder)


class CIVisibilityProxiedCoverageClient(CIVisibilityCoverageClient):
    ENDPOINT = EVP_PROXY_COVERAGE_ENDPOINT


class CIVisibilityAgentlessCoverageClient(CIVisibilityCoverageClient):
    ENDPOINT = AGENTLESS_COVERAGE_ENDPOINT


class CIVisibilityAgentlessEventClient(CIVisibilityEventClient):
    ENDPOINT = AGENTLESS_ENDPOINT


class CIVisibilityProxiedEventClient(CIVisibilityEventClient):
    ENDPOINT = EVP_PROXY_AGENT_ENDPOINT


class CIVisibilityWriter(HTTPWriter):
    RETRY_ATTEMPTS = 5
    HTTP_METHOD = "POST"
    STATSD_NAMESPACE = "civisibility.writer"

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
        coverage_enabled=False,  # type: bool
        itr_suite_skipping_mode=False,  # type: bool
    ):
        intake_cov_url = None
        if use_evp:
            intake_url = agent.get_trace_url()
            intake_cov_url = agent.get_trace_url()
        elif config._ci_visibility_agentless_url:
            intake_url = config._ci_visibility_agentless_url
            intake_cov_url = config._ci_visibility_agentless_url
        if not intake_url:
            intake_url = "%s.%s" % (AGENTLESS_BASE_URL, os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE))

        clients = (
            [CIVisibilityProxiedEventClient()] if use_evp else [CIVisibilityAgentlessEventClient()]
        )  # type: List[WriterClientBase]
        if coverage_enabled:
            if not intake_cov_url:
                intake_cov_url = "%s.%s" % (AGENTLESS_COVERAGE_BASE_URL, os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE))
            clients.append(
                CIVisibilityProxiedCoverageClient(
                    intake_url=intake_cov_url,
                    headers={EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_COVERAGE_VALUE},
                    itr_suite_skipping_mode=itr_suite_skipping_mode,
                )
                if use_evp
                else CIVisibilityAgentlessCoverageClient(
                    intake_url=intake_cov_url, itr_suite_skipping_mode=itr_suite_skipping_mode
                )
            )

        super(CIVisibilityWriter, self).__init__(
            intake_url=intake_url,
            clients=clients,
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
