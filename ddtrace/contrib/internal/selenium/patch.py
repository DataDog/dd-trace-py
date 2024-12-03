import os
import time
import typing as t

from wrapt.importer import when_imported

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.wrapping.context import WrappingContext
import ddtrace.tracer


if t.TYPE_CHECKING:
    import selenium.webdriver.remote.webdriver

log = get_logger(__name__)

T = t.TypeVar("T")

_RUM_STOP_SESSION_SCRIPT = """
if (window.DD_RUM && window.DD_RUM.stopSession) {
    window.DD_RUM.stopSession();
    return true;
} else {
    return false;
}
"""

_DEFAULT_FLUSH_SLEEP_MS = 500

_WRAPPING_CONTEXTS_STORES = set()


def _get_flush_sleep_ms() -> int:
    env_flush_sleep_ms = os.getenv("DD_CIVISIBILITY_RUM_FLUSH_WAIT_MILLIS")
    if env_flush_sleep_ms is None:
        return _DEFAULT_FLUSH_SLEEP_MS

    try:
        return int(env_flush_sleep_ms)
    except Exception:  # noqa E722
        log.warning(
            "Could not convert DD_CIVISIBILITY_RUM_FLUSH_WAIT_MILLIS value %s to int, using default: %s",
            env_flush_sleep_ms,
            _DEFAULT_FLUSH_SLEEP_MS,
        )
        return _DEFAULT_FLUSH_SLEEP_MS


config._add(
    "selenium",
    dict(flush_sleep_ms=_get_flush_sleep_ms()),
)


class SeleniumWrappingContextBase(WrappingContext):
    def _handle_enter(self) -> None:
        pass

    def _handle_return(self) -> None:
        pass

    def _get_webdriver_instance(self) -> "selenium.webdriver.remote.webdriver.WebDriver":
        try:
            return self.get_local("self")
        except KeyError:
            log.debug("Could not get Selenium WebDriver instance")
            return None

    def __enter__(self) -> None:
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
    def _handle_return(self) -> None:
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

        root_span.set_tag("test.is_browser", "true")
        root_span.set_tag("test.browser.driver", "selenium")
        root_span.set_tag("test.browser.driver_version", get_version())

        # Submit empty values for browser names or version if multiple are found
        browser_name = webdriver_instance.capabilities.get("browserName")
        browser_version = webdriver_instance.capabilities.get("browserVersion")

        existing_browser_name = root_span.get_tag("test.browser.name")
        if existing_browser_name is None:
            root_span.set_tag("test.browser.name", browser_name)
        elif existing_browser_name not in ["", browser_name]:
            root_span.set_tag("test.browser.name", "")

        existing_browser_version = root_span.get_tag("test.browser.version")
        if existing_browser_version is None:
            root_span.set_tag("test.browser.version", browser_version)
        elif existing_browser_version not in ["", browser_version]:
            root_span.set_tag("test.browser.version", "")


class SeleniumQuitWrappingContext(SeleniumWrappingContextBase):
    def _handle_enter(self) -> None:
        root_span = ddtrace.tracer.current_root_span()

        if root_span is None or root_span.get_tag("type") != "test":
            return

        webdriver_instance = self._get_webdriver_instance()

        if webdriver_instance is None:
            return

        is_rum_active = webdriver_instance.execute_script(_RUM_STOP_SESSION_SCRIPT)
        time.sleep(config.selenium.flush_sleep_ms / 1000)

        if is_rum_active:
            root_span.set_tag("test.is_rum_active", "true")

        webdriver_instance.delete_cookie("datadog-ci-visibility-test-execution-id")


def get_version() -> str:
    import selenium

    return selenium.__version__


def patch() -> None:
    import selenium

    if getattr(selenium, "_datadog_patch", False):
        return

    @when_imported("selenium.webdriver.remote.webdriver")
    def _(m):
        global _WRAPPING_CONTEXTS_STORES
        for wrapper_tup in [
            (SeleniumGetWrappingContext, m.WebDriver.get),
            (SeleniumQuitWrappingContext, m.WebDriver.quit),
            (SeleniumQuitWrappingContext, m.WebDriver.close),
        ]:
            wrapper = wrapper_tup[0](wrapper_tup[1])
            wrapper.wrap()
            _WRAPPING_CONTEXTS_STORES.add(wrapper)

    selenium._datadog_patch = True


def unpatch() -> None:
    import selenium

    if not getattr(selenium, "_datadog_patch", False):
        return

    global _WRAPPING_CONTEXTS_STORES
    for wrapper in _WRAPPING_CONTEXTS_STORES:
        wrapper.unwrap()

    selenium._datadog_patch = False
