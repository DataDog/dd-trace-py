import sys
import platform
from subprocess import PIPE
from subprocess import Popen
from time import sleep

import pytest

from tests.utils import requires_sudo
from tests.utils import run_echion


# This test requires sudo on unix to work
@requires_sudo
@pytest.mark.xfail(condition=platform.system() == "Darwin", reason="Times out on GitHub Actions")
def test_where():
    with Popen(
        [sys.executable, "-m", "tests.target_attach"], stdout=PIPE, stderr=PIPE
    ) as target:
        sleep(1)
        try:
            # attach multiple times
            for _ in range(10):
                result = run_echion("-w", str(target.pid))
                assert result.returncode == 0

                err = result.stdout.decode()

                assert "🐴 Echion reporting for duty" in err
                assert "🧵 MainThread:" in err
                assert "🧵 echion.core.sampler" in err
                assert "_run_module_as_main" in err
                assert "main" in err

                sleep(0.1)
        finally:
            target.kill()
