from http.client import RemoteDisconnected
import os
import socket
from typing import Dict
from typing import Optional

import ddtrace
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.ext.test import TEST_SESSION_NAME
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.time import StopWatch
from ddtrace.vendor.dogstatsd import DogStatsd

from .. import agent
from .. import service
from ..runtime import get_runtime_id
from ..writer import HTTPWriter
from ..writer import WriterClientBase
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
from .telemetry.payload import REQUEST_ERROR_TYPE
from .telemetry.payload import record_endpoint_payload_bytes
from .telemetry.payload import record_endpoint_payload_request
from .telemetry.payload import record_endpoint_payload_request_error
from .telemetry.payload import record_endpoint_payload_request_time


log = get_logger(__name__)


class CIVisibilityEventClient(WriterClientBase):
    def __init__(self) -> None:
        encoder = CIVisibilityEncoderV01(0, 0)
        encoder.set_metadata(
            "*",
            {
                "language": "python",
                "env": os.getenv("_CI_DD_ENV", config.env),
                "runtime-id": get_runtime_id(),
                "library_version": ddtrace.__version__,
                "_dd.test.is_user_provided_service": "true" if config._is_user_provided_service else "false",
            },
        )
        super().__init__(encoder)

    def set_metadata(self, event_type: str, metadata: Dict[str, str]) -> None:
        if isinstance(self.encoder, CIVisibilityEncoderV01):
            self.encoder.set_metadata(event_type, metadata)

    def set_test_session_name(self, test_session_name: str) -> None:
        for event_type in [SESSION_TYPE, MODULE_TYPE, SUITE_TYPE, SpanTypes.TEST]:
            self.set_metadata(event_type, {TEST_SESSION_NAME: test_session_name})


class CIVisibilityCoverageClient(WriterClientBase):
    def __init__(
        self, intake_url: str, headers: Optional[Dict[str, str]] = None, itr_suite_skipping_mode: bool = False
    ) -> None:
        encoder = CIVisibilityCoverageEncoderV02(0, 0)
        if itr_suite_skipping_mode:
            encoder._set_itr_suite_skipping_mode(itr_suite_skipping_mode)
        self._intake_url = intake_url
        if headers:
            self._headers = headers
        super().__init__(encoder)


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
        intake_url: str = "",
        processing_interval: Optional[float] = None,
        timeout: Optional[float] = None,
        dogstatsd: Optional[DogStatsd] = None,
        sync_mode: bool = False,
        report_metrics: bool = False,  # unused, but required for inheritance
        api_version: Optional[str] = None,  # unused, but required for inheritance
        reuse_connections: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
        use_evp: bool = False,
        coverage_enabled: bool = False,
        itr_suite_skipping_mode: bool = False,
    ):
        if processing_interval is None:
            processing_interval = config._trace_writer_interval_seconds
        if timeout is None:
            timeout = config._agent_timeout_seconds
        intake_cov_url = None
        if use_evp:
            intake_url = intake_url if intake_url else agent.get_trace_url()
            intake_cov_url = intake_url
        elif config._ci_visibility_agentless_url:
            intake_url = intake_url if intake_url else config._ci_visibility_agentless_url
            intake_cov_url = intake_url
        if not intake_url:
            intake_url = "%s.%s" % (AGENTLESS_BASE_URL, os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE))

        ## Validate API key if headers are provided
        # if headers and "dd-api-key" in headers and not headers["dd-api-key"]:
        #     log.warning("Empty API key provided to CIVisibilityWriter. This may cause authentication issues.")
        #     # Remove empty API key to prevent sending empty credentials
        #     headers = {k: v for k, v in headers.items() if k != "dd-api-key"}

        clients: list[WriterClientBase] = (
            [CIVisibilityProxiedEventClient()] if use_evp else [CIVisibilityAgentlessEventClient()]
        )
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

        super().__init__(
            intake_url=intake_url,
            clients=clients,
            processing_interval=processing_interval,
            timeout=timeout,
            dogstatsd=dogstatsd,
            sync_mode=sync_mode,
            report_metrics=report_metrics,
            reuse_connections=reuse_connections,
            headers=headers,
        )

    def stop(self, timeout: Optional[float] = None) -> None:
        if self.status != service.ServiceStatus.STOPPED:
            super().stop(timeout=timeout)

    def recreate(self) -> "CIVisibilityWriter":
        return self.__class__(
            intake_url=self.intake_url,
            processing_interval=self._interval,
            timeout=self._timeout,
            dogstatsd=self.dogstatsd,
            sync_mode=self._sync_mode,
        )

    def _put(self, data: bytes, headers: Dict[str, str], client: WriterClientBase, no_trace: bool) -> Response:
        request_error: Optional[REQUEST_ERROR_TYPE] = None

        with StopWatch() as sw:
            try:
                response = super()._put(data, headers, client, no_trace)
                if response.status >= 400:
                    request_error = REQUEST_ERROR_TYPE.STATUS_CODE
            except (TimeoutError, socket.timeout):
                request_error = REQUEST_ERROR_TYPE.TIMEOUT
                raise
            except RemoteDisconnected:
                request_error = REQUEST_ERROR_TYPE.NETWORK
                raise
            finally:
                if isinstance(client.encoder, CIVisibilityEncoderV01):
                    endpoint = client.encoder.ENDPOINT_TYPE
                    record_endpoint_payload_bytes(endpoint, nbytes=len(data))
                    record_endpoint_payload_request(endpoint)
                    record_endpoint_payload_request_time(endpoint, seconds=sw.elapsed())
                    if request_error:
                        record_endpoint_payload_request_error(endpoint, request_error)

        return response
