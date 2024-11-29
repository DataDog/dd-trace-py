import http.server
import multiprocessing
import os
from pathlib import Path
import socketserver
import subprocess
import textwrap

import pytest

from tests.ci_visibility.util import _get_default_ci_env_vars
from tests.utils import snapshot


SNAPSHOT_IGNORES = [
    "meta.ci.workspace_path",
    "meta.error.stack",
    "meta.library_version",
    "meta.os.architecture",
    "meta.os.platform",
    "meta.os.version",
    "meta.runtime-id",
    "meta.runtime.version",
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
def _http_server():
    """Provides a simple HTTP server that servers the pages to be browser

    We use an HTTP server because RUM does not work with file:// URLs (it's unable to establish session storage)
    """

    def _run_server():
        server_root = Path(__file__).parent / "static_test_pages"
        os.chdir(server_root)
        with socketserver.TCPServer(("localhost", 8079), http.server.SimpleHTTPRequestHandler) as httpd:
            print("serving at port", 8079)
            httpd.serve_forever()

    server = multiprocessing.Process(target=_run_server)
    server.start()
    yield
    if server.is_alive():
        server.terminate()


@snapshot(ignores=SNAPSHOT_IGNORES)
def test_selenium_chromium_pytest_rum_enabled(_http_server, testdir, git_repo):
    selenium_test_script = textwrap.dedent(
        """
        from pathlib import Path

        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        def test_selenium_local_pass():
            options = webdriver.ChromeOptions()
            options.add_argument("--headless")

            with webdriver.Chrome(service=Service(ChromeDriverManager().install())) as driver:
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
        ["pytest", "--ddtrace", "-s"],
        env=_get_default_ci_env_vars(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_ITR_ENABLED="false",
                DD_PATCH_MODULES="sqlite3:false",
                CI_PROJECT_DIR=str(testdir.tmpdir),
                DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
            )
        ),
    )
