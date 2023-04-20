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
from ddtrace.internal import compat
from ddtrace.internal.agent import get_connection
from ddtrace.internal.ci_visibility.filters import TraceCiVisibilityFilter
from ddtrace.internal.compat import parse
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.internal.writer.writer import Response
from ddtrace.settings import IntegrationConfig

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


def _do_request(method, url, payload, headers):
    # type: (str, str, str, Dict) -> Response
    try:
        conn = get_connection(url)
        log.debug("Sending request: %s %s %s %s", (method, url, payload, headers))
        conn.request("POST", url, payload, headers)
        resp = compat.get_connection_response(conn)
        log.debug("Response status: %s", resp.status)
    finally:
        conn.close()
    return Response.from_http_response(resp)


class CIVisibility(Service):
    _instance = None  # type: Optional[CIVisibility]
    enabled = False

    def __init__(self, tracer=None, config=None, service=None):
        # type: (Optional[Tracer], Optional[IntegrationConfig], Optional[str]) -> None
        super(CIVisibility, self).__init__()

        self.tracer = tracer or ddtrace.tracer
        if ddconfig._ci_visibility_agentless_enabled and not isinstance(self.tracer._writer, CIVisibilityWriter):
            writer = CIVisibilityWriter()
            self.tracer.configure(writer=writer)
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

    @classmethod
    def code_coverage_enabled(cls):
        if not cls.enabled:
            return False
        return getattr(cls._instance, "_code_coverage_enabled_by_api", False)

    @classmethod
    def test_skipping_enabled(cls):
        if not cls.enabled:
            return False
        return getattr(cls._instance, "_test_skipping_enabled_by_api", False)

    @classmethod
    def get_tests_to_skip(cls, suite, module):
        payload = {
            "data": {
                "type": "test_params",
                "attributes": {
                    "service": "dd-trace-go",
                    "env": "testing-emmett",
                    "repository_url": "https://github.com/DataDog/dd-trace-go",
                    "sha": "a00d7e59a6fab34c4a79a9ed5388c9b68511622b",
                    "suite": "gopkg.in/DataDog/dd-trace-go.v1/ddtrace/tracer",
                    "configurations": {
                        "os.platform": "Linux",
                        "os.architecture": "amd64",
                        "os.version": "410",
                        "runtime.name": "CPython",
                        "runtime.version": "100",
                    },
                },
            }
        }
        url = "https://api.datadoghq.com/api/v2/ci/tests/skippable"
        headers = {"dd-api-key": os.getenv("DD_API_KEY"), "dd-application-key": os.getenv("DD_APP_KEY")}
        response = _do_request("POST", url, payload, headers)
        if response.status >= 400:
            log.warning("Test skips request responded with status %d", response.status)
            return []
        try:
            parsed = json.loads(response.body)
        except json.JSONDecodeError:
            log.warning("Test skips request responded with invalid JSON '%s'", response.body)
            return []
        tests_to_skip = []
        for item in parsed["data"]:
            attributes = item["attributes"]
            if item["type"] == "test":
                tests_to_skip.append((attributes["suite"], attributes["name"], attributes["parameters"]))
        return tests_to_skip

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
