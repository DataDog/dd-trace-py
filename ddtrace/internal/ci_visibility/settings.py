from ddtrace.internal.ci_visibility.constants import AGENTLESS_DEFAULT_SITE
from ddtrace.settings._core import DDConfig


DEFAULT_TIMEOUT = 15
DEFAULT_ITR_SKIPPABLE_TIMEOUT = 20


class CIEnv(DDConfig):
    """Class encapsulating CI environment variables and their access."""

    # Prefix for all CI environment variables
    __prefix__ = "ci"

    class DDEnv(DDConfig):
        """Class encapsulating CI DD environment variables and their access.

        These are internal environment variables primarily used for testing
        and CI/CD pipelines within DataDog.
        """

        # Prefix for CI_DD environment variables
        __prefix__ = "dd"

        # Agent URL
        agent_url = DDConfig.v(str, "agent_url", private=True, default="", help="Internal agent URL used for testing")

        # Authentication
        api_key = DDConfig.v(str, "api_key", private=True, default="", help="Internal API key used for testing")

        # Env
        env = DDConfig.v(str, "env", private=True, default="", help="Environment name for CI/CD pipelines")


class TestOptEnv(DDConfig):
    """Class encapsulating Test Optimization environment variables and their access."""

    # Prefix for all environment variables
    __prefix__ = "dd"

    # Used in recorder.py
    api_key = DDConfig.v(str, "api_key", default="")
    site = DDConfig.v(str, "site", default=AGENTLESS_DEFAULT_SITE)

    class CIVisibilityEnv(DDConfig):
        """Class encapsulating CI Visibility environment variables and their access."""

        # Prefix for CI Visibility environment variables
        __prefix__ = "civisibility"

        # ITR-related settings
        itr_suite_mode = DDConfig.v(bool, "itr_suite_mode", private=True, default=False)
        itr_force_enable_coverage = DDConfig.v(bool, "itr_force_enable_coverage", default=False, private=True)
        itr_prevent_test_skipping = DDConfig.v(bool, "itr_prevent_test_skipping", default=False, private=True)

        # Flaky test retries
        flaky_retry_enabled = DDConfig.v(bool, "flaky_retry_enabled", default=True)
        flaky_retry_count = DDConfig.v(int, "flaky_retry_count", default=5)
        total_flaky_retry_count = DDConfig.v(int, "total_flaky_retry_count", default=1000)

        # Test management
        test_management_enabled = DDConfig.v(bool, "test_management_enabled", default=True)
        test_management_attempt_to_fix_retries = DDConfig.v(int, "test_management_attempt_to_fix_retries", default=20)

        # Context provider
        use_ci_context_provider = DDConfig.v(bool, "use_ci_context_provider", private=True, default=False)
        auto_instrumentation_provider = DDConfig.v(bool, "auto_instrumentation_provider", default=False)

        # UI testing
        rum_flush_wait_millis = DDConfig.v(int, "rum_flush_wait_millis", default=500)

        # Unittest settings
        unittest_strict_naming = DDConfig.v(bool, "unittest_strict_naming", default=True)
