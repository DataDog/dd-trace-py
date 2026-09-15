"""ddtest-specific environment helpers for test subprocesses.

Encapsulates env var stripping so that removing ddtest support is a simple
matter of deleting this file and reverting the import + call site in
tests/utils.py.
"""

# PYTEST_ADDOPTS is set to "--ddtrace" by ddtest's platform env (ddtest/internal/
# platform/python.go:GetPlatformEnv) so the pytest workers load the ddtrace
# testing plugin. It leaks into every test-spawned subprocess via env
# inheritance. Under normal riot CI this var is absent, so test subprocesses
# don't activate CI Visibility. The leaked --ddtrace makes a nested
# pytest.main() enable the plugin, which logs INFO to stderr (breaking tests
# that assert err == b"") and computes stats (breaking snapshot tests). No
# test sets PYTEST_ADDOPTS via call_program's env kwarg, so stripping it here
# is safe. (ddtest's main.go also sets DD_CIVISIBILITY_ENABLED=1 globally, but
# that var is inert on its own — the pytest plugin needs --ddtrace to activate
# — so stripping PYTEST_ADDOPTS is sufficient to keep subprocesses
# CI-Visibility-free.)
_DDTEST_LEAKED_ENV_VARS = ("PYTEST_ADDOPTS",)


def strip_ddtest_leaked_env(env):
    """Remove env vars leaked from the ddtest parent worker."""
    env = dict(env)
    for key in _DDTEST_LEAKED_ENV_VARS:
        env.pop(key, None)
    return env
