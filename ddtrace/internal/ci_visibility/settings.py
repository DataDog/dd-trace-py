from envier import Env

from ddtrace.internal.ci_visibility.constants import AGENTLESS_DEFAULT_SITE


DEFAULT_TIMEOUT = 15
DEFAULT_ITR_SKIPPABLE_TIMEOUT = 20


class CIEnv(Env):
    """Class encapsulating CI environment variables and their access."""

    # Prefix for all CI environment variables
    __prefix__ = "CI"

    class DDEnv(Env):
        """Class encapsulating CI DD environment variables and their access.

        These are internal environment variables primarily used for testing
        and CI/CD pipelines within DataDog.
        """

        # Prefix for CI_DD environment variables
        __prefix__ = "DD"

        # Agent URL
        agent_url = Env.var(str, "agent_url", private=True, default="", help="Internal agent URL used for testing")

        # Authentication
        api_key = Env.var(str, "api_key", private=True, default=None, help="Internal API key used for testing")

        # Env
        env = Env.var(str, "env", private=True, default=None, help="Environment name for CI/CD pipelines")


class TestOptEnv(Env):
    """Class encapsulating Test Optimization environment variables and their access."""

    # Prefix for all environment variables
    __prefix__ = "DD"

    # Used in recorder.py
    api_key = Env.var(str, "api_key", default=None)
    site = Env.var(str, "site", default=AGENTLESS_DEFAULT_SITE)

    class CIVisibilityEnv(Env):
        """Class encapsulating CI Visibility environment variables and their access."""

        # Prefix for CI Visibility environment variables
        __prefix__ = "CIVISIBILITY"

        # ITR-related settings
        itr_suite_mode = Env.var(bool, "itr_suite_mode", private=True, default=False)
        itr_force_enable_coverage = Env.var(bool, "itr_force_enable_coverage", default=False, private=True)
        itr_prevent_test_skipping = Env.var(bool, "itr_prevent_test_skipping", default=False, private=True)

        # Flaky test retries
        flaky_retry_enabled = Env.var(bool, "flaky_retry_enabled", default=True)
        flaky_retry_count = Env.var(int, "flaky_retry_count", default=5)
        total_flaky_retry_count = Env.var(int, "total_flaky_retry_count", default=1000)

        # Test management
        test_management_enabled = Env.var(bool, "test_management_enabled", default=True)
        test_management_attempt_to_fix_retries = Env.var(int, "test_management_attempt_to_fix_retries", default=20)

        # Context provider
        use_ci_context_provider = Env.var(bool, "use_ci_context_provider", private=True, default=False)
        auto_instrumentation_provider = Env.var(str, "auto_instrumentation_provider", default=None)

        # UI testing
        rum_flush_wait_millis = Env.var(str, "rum_flush_wait_millis", default=None)

        # Unittest settings
        unittest_strict_naming = Env.var(bool, "unittest_strict_naming", default=True)
