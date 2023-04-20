import json
import os
from typing import Any
from typing import Dict
from typing import Optional

import ddtrace
from ddtrace import Tracer
from ddtrace import config as ddconfig
from ddtrace.contrib import trace_utils
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.internal import atexit
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.compat import parse
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.settings import IntegrationConfig

from .. import agent
from .constants import AGENTLESS_BASE_URL
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import EVP_PROXY_AGENT_BASE_PATH
from .constants import EVP_SUBDOMAIN_HEADER_NAME
from .constants import EVP_SUBDOMAIN_HEADER_VALUE
from .writer import CIVisibilityWriter


log = get_logger(__name__)


def _extract_repository_name_from_url(repository_url):
    # type: (str) -> str
    try:
        return parse.urlparse(repository_url).path.rstrip(".git").rpartition("/")[-1]
    except ValueError:
        # In case of parsing error, default to repository url
        log.warning("Repository name cannot be parsed from repository_url: %s", repository_url)
        return repository_url


def _get_git_repo():
    # this exists only for the purpose of patching in tests
    return None


class CIVisibility(Service):
    _instance = None  # type: Optional[CIVisibility]
    enabled = False

    def __init__(self, tracer=None, config=None, service=None):
        # type: (Optional[Tracer], Optional[IntegrationConfig], Optional[str]) -> None
        super(CIVisibility, self).__init__()

        self.tracer = tracer or ddtrace.tracer
        self._configure_writer()
        self.config = config  # type: Optional[IntegrationConfig]
        self._tags = ci.tags(cwd=_get_git_repo())  # type: Dict[str, str]
        self._service = service
        self._codeowners = None

        int_service = None
        if self.config is not None:
            int_service = trace_utils.int_service(None, self.config)
        # check if repository URL detected from environment or .git, and service name unchanged
        if self._tags.get(ci.git.REPOSITORY_URL, None) and self.config and int_service == self.config._default_service:
            self._service = _extract_repository_name_from_url(self._tags[ci.git.REPOSITORY_URL])
        elif self._service is None and int_service is not None:
            self._service = int_service

        try:
            from ddtrace.internal.codeowners import Codeowners

            self._codeowners = Codeowners()
        except ValueError:
            log.warning("CODEOWNERS file is not available")
        except Exception:
            log.warning("Failed to load CODEOWNERS", exc_info=True)

    def _configure_writer(self):
        writer = None
        if ddconfig._ci_visibility_agentless_enabled:
            headers = {"dd-api-key": os.environ.get("DD_API_KEY")}
            if headers["dd-api-key"]:
                writer = CIVisibilityWriter(
                    intake_url="%s.%s" % (AGENTLESS_BASE_URL, os.environ.get("DD_SITE", AGENTLESS_DEFAULT_SITE)),
                    headers=headers,
                )
            else:
                raise EnvironmentError(
                    "DD_CIVISIBILITY_AGENTLESS_ENABLED is set, but DD_API_KEY is not set, so ddtrace "
                    "cannot be initialized."
                )
        elif self._agent_evp_proxy_is_available():
            writer = CIVisibilityWriter(
                intake_url=agent.get_trace_url(),
                headers={EVP_SUBDOMAIN_HEADER_NAME: EVP_SUBDOMAIN_HEADER_VALUE},
                use_evp=True,
            )
        if writer is not None:
            self.tracer.configure(writer=writer)

    def _agent_evp_proxy_is_available(self):
        try:
            info = agent.info()
        except Exception:
            info = None

        if info:
            endpoints = info.get("endpoints", [])
            if endpoints and any(EVP_PROXY_AGENT_BASE_PATH in endpoint for endpoint in endpoints):
                return True
        return False

    @classmethod
    def enable(cls, tracer=None, config=None, service=None):
        # type: (Optional[Tracer], Optional[Any], Optional[str]) -> None

        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return
        log.debug("Enabling %s", cls.__name__)

        cls._instance = cls(tracer=tracer, config=config, service=service)
        cls.enabled = True

        cls._instance.start()
        atexit.register(cls.disable)

        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls):
        # type: () -> None
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return
        log.debug("Disabling %s", cls.__name__)
        atexit.unregister(cls.disable)

        cls._instance.stop()
        cls._instance = None
        cls.enabled = False

        log.debug("%s disabled", cls.__name__)

    def _start_service(self):
        # type: () -> None
        tracer_filters = self.tracer._filters
        if not any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in tracer_filters):
            tracer_filters += [TraceCiVisibilityFilter(self._tags, self._service)]  # type: ignore[arg-type]
            self.tracer.configure(settings={"FILTERS": tracer_filters})

    def _stop_service(self):
        # type: () -> None
        try:
            self.tracer.shutdown()
        except Exception:
            log.warning("Failed to shutdown tracer", exc_info=True)

    @classmethod
    def set_codeowners_of(cls, location, span=None):
        if not cls.enabled or cls._instance is None or cls._instance._codeowners is None or not location:
            return

        span = span or cls._instance.tracer.current_span()
        if span is None:
            return

        try:
            handles = cls._instance._codeowners.of(location)
            if handles:
                span.set_tag(test.CODEOWNERS, json.dumps(handles))
        except KeyError:
            log.debug("no matching codeowners for %s", location)
