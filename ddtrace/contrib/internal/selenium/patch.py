import os
import time
import typing as t

import selenium
from selenium.webdriver.remote.webdriver import WebDriver

from ddtrace.internal.logger import get_logger
from ddtrace.internal.wrapping.context import WrappingContext
import ddtrace.tracer


log = get_logger(__name__)

T = t.TypeVar("T")

RUM_STOP_SESSION_SCRIPT = """
if (window.DD_RUM && window.DD_RUM.stopSession) {
    window.DD_RUM.stopSession();
    return true;
} else {
    return false;
}
"""

FLUSH_SLEEP_MS = int(os.getenv("DD_CIVISIBILITY_RUM_FLUSH_WAIT_MILLIS", "500"))


class SeleniumWrappingContextBase(WrappingContext):
    def _handle_enter(self):
        pass

    def _handle_return(self):
        pass

    def _get_webdriver_instance(self):
        try:
            return self.get_local("self")
        except KeyError:
            log.debug("Could not get Selenium WebDriver instance")
            return None

    def __enter__(self):
        try:
            self._handle_enter()
        except Exception:  # noqa: E722
            log.debug("Error handling selenium instrumentation enter", exc_info=True)

    def __return__(self, value: T) -> T:
        """Always return the original value no matter what our instrumentation does"""
        try:
            self._handle_return()
        except Exception:  # noqa: E722
            log.debug("Error handling instrumentation return", exc_info=True)

        return value


class SeleniumGetWrappingContext(SeleniumWrappingContextBase):
    def _handle_return(self):
        root_span = ddtrace.tracer.current_root_span()
        test_trace_id = root_span.trace_id

        if root_span is None or root_span.get_tag("type") != "test":
            return

        webdriver_instance = self._get_webdriver_instance()

        if webdriver_instance is None:
            return

        # The trace IDs for Test Visibility data using the CIVisibility protocol are 64-bit
        # TODO[ci_visibility]: properly identify whether to use 64 or 128 bit trace_ids
        trace_id_64bit = test_trace_id % 2**64

        webdriver_instance.add_cookie({"name": "datadog-ci-visibility-test-execution-id", "value": str(trace_id_64bit)})

        root_span.set_tag("test.is_browser", True)
        root_span.set_tag("test.browser.driver", "selenium")
        root_span.set_tag("test.browser.driver_version", get_version())

        # Submit empty values for browser names or version if multiple are found
        browser_name = webdriver_instance.capabilities.get("browserName")
        browser_version = webdriver_instance.capabilities.get("browserVersion")

        existing_browser_name = root_span.get_tag("test.browser.name")
        if existing_browser_name is None:
            root_span.set_tag("test.browser.name")
        elif existing_browser_name not in ["", browser_name]:
            root_span.set_tag("test.browser.name", "")

        existing_browser_version = root_span.get_tag("test.browser.version")
        if existing_browser_version is None:
            root_span.set_tag("test.browser.version", browser_version)
        elif existing_browser_version not in ["", browser_version]:
            root_span.set_tag("test.browser.version", "")


class SeleniumQuitWrappingContext(SeleniumWrappingContextBase):
    def _handle_enter(self):
        root_span = ddtrace.tracer.current_root_span()

        if root_span is None or root_span.get_tag("type") != "test":
            return

        webdriver_instance = self._get_webdriver_instance()

        if webdriver_instance is None:
            return

        is_rum_active = webdriver_instance.execute_script(RUM_STOP_SESSION_SCRIPT)
        time.sleep(FLUSH_SLEEP_MS / 1000)

        if is_rum_active:
            root_span.set_tag("test.is_rum_active", True)

        webdriver_instance.delete_cookie("datadog-ci-visibility-test-execution-id")


def get_version():
    return selenium.__version__


def patch():
    SeleniumGetWrappingContext(WebDriver.get).wrap()
    SeleniumQuitWrappingContext(WebDriver.quit).wrap()
    SeleniumQuitWrappingContext(WebDriver.close).wrap()


def unpatch():
    SeleniumGetWrappingContext(WebDriver.get).unwrap()
    SeleniumQuitWrappingContext(WebDriver.quit).unwrap()
    SeleniumQuitWrappingContext(WebDriver.close).unwrap()
