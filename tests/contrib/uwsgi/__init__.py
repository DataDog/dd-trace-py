import os
import subprocess
from typing import Callable
from typing import Sequence


def run_uwsgi(cmd: Sequence[str]) -> Callable[..., subprocess.Popen[bytes]]:
    def _run(*args: str):
        env = os.environ.copy()
        return subprocess.Popen(
            list(cmd) + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    return _run
