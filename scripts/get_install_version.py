#!/usr/bin/env python
import subprocess
import sys


out = subprocess.run([sys.executable, "-m", "setuptools_scm"], capture_output=True, check=True)
scm_version = out.stdout.decode().strip()

if "dev" in scm_version:
    git_out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, check=True)
    git_ref = git_out.stdout.decode().strip()
    print("git+https://github.com/Datadog/dd-trace-py@%s" % git_ref)
else:
    print(scm_version)
