import subprocess
import sys
import unittest


class TestRequestsGevent(unittest.TestCase):
    def test_patch(self):
        # Since this test depends on import ordering it is run in a separate
        # process with a fresh interpreter.
        p = subprocess.Popen(
            [sys.executable, "tests/contrib/requests_gevent/run_test.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        p.wait()

        assert p.stdout.read() == b"Test succeeded\n", p.stderr.read()
