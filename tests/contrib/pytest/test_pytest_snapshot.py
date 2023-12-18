import subprocess

import pytest

from tests.utils import TracerTestCase
from tests.utils import override_env
from tests.utils import snapshot


SNAPSHOT_IGNORES = [
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

SNAPSHOT_IGNORES_ITR_COVERAGE = ["metrics.test.source.start", "metrics.test.source.end", "meta.test.source.file"]


class PytestSnapshotTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_pytest_will_include_lines_pct(self):
        tools = """
                def add_two_number_list(list_1, list_2):
                    output_list = []
                    for number_a, number_b in zip(list_1, list_2):
                        output_list.append(number_a + number_b)
                    return output_list

                def multiply_two_number_list(list_1, list_2):
                    output_list = []
                    for number_a, number_b in zip(list_1, list_2):
                        output_list.append(number_a * number_b)
                    return output_list
                """
        self.testdir.makepyfile(tools=tools)
        test_tools = """
                from tools import add_two_number_list

                def test_add_two_number_list():
                    a_list = [1,2,3,4,5,6,7,8]
                    b_list = [2,3,4,5,6,7,8,9]
                    actual_output = add_two_number_list(a_list, b_list)

                    assert actual_output == [3,5,7,9,11,13,15,17]
                """
        self.testdir.makepyfile(test_tools=test_tools)
        self.testdir.chdir()
        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
                DD_PATCH_MODULES="sqlite3:false",
            )
        ):
            subprocess.run(["ddtrace-run", "coverage", "run", "--include=tools.py", "-m", "pytest", "--ddtrace"])

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_pytest_wont_include_lines_pct_if_report_empty(self):
        tools = """
                def add_two_number_list(list_1, list_2):
                    output_list = []
                    for number_a, number_b in zip(list_1, list_2):
                        output_list.append(number_a + number_b)
                    return output_list

                def multiply_two_number_list(list_1, list_2):
                    output_list = []
                    for number_a, number_b in zip(list_1, list_2):
                        output_list.append(number_a * number_b)
                    return output_list
                """
        self.testdir.makepyfile(tools=tools)
        test_tools = """
                from tools import add_two_number_list

                def test_add_two_number_list():
                    a_list = [1,2,3,4,5,6,7,8]
                    b_list = [2,3,4,5,6,7,8,9]
                    actual_output = add_two_number_list(a_list, b_list)

                    assert actual_output == [3,5,7,9,11,13,15,17]
                """
        self.testdir.makepyfile(test_tools=test_tools)
        self.testdir.chdir()
        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
                DD_PATCH_MODULES="sqlite3:false",
            )
        ):
            subprocess.run(["ddtrace-run", "coverage", "run", "--include=nothing.py", "-m", "pytest", "--ddtrace"])
