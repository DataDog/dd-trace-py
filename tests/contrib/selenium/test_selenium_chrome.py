"""Tests for selenium + RUM integration

IMPORTANT NOTE: these tests only reliably work on Linux/x86_64 due to some annoyingly picky issues with installing
Selenium and a working browser/webdriver combination on non-x86_64 architectures (at time of writing, at least,
Selenium's webdriver-manager doesn't support Linux on non-x86_64).
"""

import http.server
import json
import multiprocessing
import os
from pathlib import Path
import platform
import socketserver
import subprocess
import textwrap

import pytest

from tests.ci_visibility.util import _get_default_ci_env_vars
from tests.utils import snapshot


SELENIUM_SNAPSHOT_IGNORES = [
    "resource",  # Ignored because v1 and v2 plugins have different results, but that's okay
    "meta.ci.workspace_path",
    "meta.error.stack",
    "meta.library_version",
    "meta.os.architecture",
    "meta.os.platform",
    "meta.os.version",
    "meta.runtime-id",
    "meta.runtime.version",
    "meta.test.browser.version",  # ignored because it may change when images are rebuilt
    "meta.test.browser.driver_version",  # ignored because it may change when images are rebuilt
    "meta.test.framework_version",
    "meta.test_module_id",
    "meta.test_session_id",
    "meta.test_suite_id",
    "metrics._dd.top_level",
    "metrics._dd.tracer_kr",
    "metrics._sampling_priority_v1",
    "metrics.process_id",
    "duration",
    "start",
]


@pytest.fixture
def _http_server(scope="function"):
    """Provides a simple HTTP server that servers the pages to be browser

    We use an HTTP server because RUM does not work with file:// URLs (it's unable to establish session storage)
    """

    def _run_server():
        server_root = Path(__file__).parent / "static_test_pages"
        os.chdir(server_root)
        # We do not use the context manager gecause we need allow_reuse_address
        httpd = socketserver.TCPServer(
            ("localhost", 8079), http.server.SimpleHTTPRequestHandler, bind_and_activate=False
        )
        httpd.allow_reuse_address = True
        httpd.daemon_threads = True
        httpd.server_bind()
        httpd.server_activate()
        httpd.serve_forever()

    server = multiprocessing.Process(target=_run_server)
    server.start()
    yield
    if server.is_alive():
        server.terminate()


@snapshot(ignores=SELENIUM_SNAPSHOT_IGNORES)
@pytest.mark.skipif(platform.machine() != "x86_64", reason="Selenium Chrome tests only run on x86_64")
def test_selenium_chrome_pytest_rum_enabled(_http_server, testdir, git_repo):
    selenium_test_script = textwrap.dedent(
        """
            from pathlib import Path

            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.chrome.options import Options

            def test_selenium_local_pass():
                options = Options()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")

                with webdriver.Chrome(options=options) as driver:
                    url = "http://localhost:8079/rum_enabled/page_1.html"

                    driver.get(url)

                    assert driver.title == "Page 1"

                    link_2 = driver.find_element(By.LINK_TEXT, "Page 2")

                    link_2.click()

                    assert driver.title == "Page 2"

                    link_1 = driver.find_element(By.LINK_TEXT, "Back to page 1.")
                    link_1.click()

                    assert driver.title == "Page 1"
        """
    )
    testdir.makepyfile(test_selenium=selenium_test_script)
    subprocess.run(
        ["pytest", "--ddtrace", "-s", "--ddtrace-patch-all"],
        env=_get_default_ci_env_vars(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_ITR_ENABLED="false",
                DD_PATCH_MODULES="sqlite3:false",
                CI_PROJECT_DIR=str(testdir.tmpdir),
                DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
                _DD_CIVISIBILITY_DISABLE_EVP_PROXY="true",
                # Snapshot test expects traces from the agent; v3 plugin uses TestOptWriter and does not send them.
                DD_PYTEST_USE_NEW_PLUGIN="false",
            )
        ),
    )


@snapshot(ignores=SELENIUM_SNAPSHOT_IGNORES)
@pytest.mark.skipif(platform.machine() != "x86_64", reason="Selenium Chrome tests only run on x86_64")
def test_selenium_chrome_pytest_rum_disabled(_http_server, testdir, git_repo):
    selenium_test_script = textwrap.dedent(
        """
            from pathlib import Path

            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.chrome.options import Options

            def test_selenium_local_pass():
                options = Options()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")

                with webdriver.Chrome(options=options) as driver:
                    url = "http://localhost:8079/rum_disabled/page_1.html"

                    driver.get(url)

                    assert driver.title == "Page 1"

                    link_2 = driver.find_element(By.LINK_TEXT, "Page 2")

                    link_2.click()

                    assert driver.title == "Page 2"

                    link_1 = driver.find_element(By.LINK_TEXT, "Back to page 1.")
                    link_1.click()

                    assert driver.title == "Page 1"
        """
    )
    testdir.makepyfile(test_selenium=selenium_test_script)
    subprocess.run(
        ["pytest", "--ddtrace", "-s", "--ddtrace-patch-all"],
        env=_get_default_ci_env_vars(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_ITR_ENABLED="false",
                DD_PATCH_MODULES="sqlite3:false",
                CI_PROJECT_DIR=str(testdir.tmpdir),
                DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
                _DD_CIVISIBILITY_DISABLE_EVP_PROXY="true",
                # Snapshot test expects traces from the agent; v3 plugin uses TestOptWriter and does not send them.
                DD_PYTEST_USE_NEW_PLUGIN="false",
            )
        ),
    )


@snapshot(ignores=SELENIUM_SNAPSHOT_IGNORES)
@pytest.mark.skipif(platform.machine() != "x86_64", reason="Selenium Chrome tests only run on x86_64")
def test_selenium_chrome_pytest_unpatch_does_not_record_selenium_tags(_http_server, testdir, git_repo):
    selenium_test_script = textwrap.dedent(
        """
            from pathlib import Path

            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.chrome.options import Options

            from ddtrace.contrib.internal.selenium.patch import unpatch

            def test_selenium_local_unpatch():
                unpatch()
                options = Options()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")

                with webdriver.Chrome(options=options) as driver:
                    url = "http://localhost:8079/rum_disabled/page_1.html"

                    driver.get(url)

                    assert driver.title == "Page 1"

                    link_2 = driver.find_element(By.LINK_TEXT, "Page 2")

                    link_2.click()

                    assert driver.title == "Page 2"

                    link_1 = driver.find_element(By.LINK_TEXT, "Back to page 1.")
                    link_1.click()

                    assert driver.title == "Page 1"
        """
    )
    testdir.makepyfile(test_selenium=selenium_test_script)
    subprocess.run(
        ["pytest", "--ddtrace", "-s", "--ddtrace-patch-all"],
        env=_get_default_ci_env_vars(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_ITR_ENABLED="false",
                DD_PATCH_MODULES="sqlite3:false",
                CI_PROJECT_DIR=str(testdir.tmpdir),
                DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
                _DD_CIVISIBILITY_DISABLE_EVP_PROXY="true",
                # Snapshot test expects traces from the agent; v3 plugin uses TestOptWriter and does not send them.
                DD_PYTEST_USE_NEW_PLUGIN="false",
            )
        ),
    )


def test_selenium_v3_plugin_tags(tmp_path, pytester, git_repo):
    events_file = tmp_path / "events.json"

    # conftest.py that captures events emitted by the v3 plugin to a JSON file
    pytester.makeconftest(
        f"""
import json, atexit
from ddtrace.testing.internal.writer import TestOptWriter

_events = []
_orig_put = TestOptWriter.put_event
def _capture(self, event):
    _events.append(event)
    return _orig_put(self, event)
TestOptWriter.put_event = _capture

@atexit.register
def _dump():
    with open(r"{events_file}", "w") as f:
        json.dump(_events, f, default=str)
"""
    )

    pytester.makepyfile(
        test_selenium="""
from selenium.webdriver.remote.webdriver import WebDriver

def test_selenium_browser_tags():
    driver = WebDriver.__new__(WebDriver)
    driver.caps = {"browserName": "chrome", "browserVersion": "120.0"}
    driver.add_cookie = lambda cookie: None
    driver.execute = lambda *a, **kw: None

    driver.get("http://example.com")
"""
    )

    subprocess.run(
        ["pytest", "--ddtrace", "--ddtrace-patch-all", "-v", "-s"],
        cwd=str(pytester.path),
        env=_get_default_ci_env_vars(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_ITR_ENABLED="false",
                DD_PATCH_MODULES="sqlite3:false",
                CI_PROJECT_DIR=str(pytester.path),
                DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
                _DD_CIVISIBILITY_DISABLE_EVP_PROXY="true",
                DD_PYTEST_USE_NEW_PLUGIN="true",
            )
        ),
    )

    events = json.loads(events_file.read_text())
    test_events = [e for e in events if e["type"] == "test"]
    meta = test_events[0]["content"]["meta"]
    assert meta["test.is_browser"] == "true"
    assert meta["test.browser.driver"] == "selenium"
    assert meta["test.browser.name"] == "chrome"
    assert meta["test.browser.version"] == "120.0"
