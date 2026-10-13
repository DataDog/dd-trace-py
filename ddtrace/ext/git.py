# Moved to ddtrace_base.ext.git; this shim keeps the public import path working.
from ddtrace_base.ext import git as _git  # noqa: F401
from ddtrace_base.ext.git import *  # noqa: F401,F403
