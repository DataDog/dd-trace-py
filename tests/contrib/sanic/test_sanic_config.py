from tests.base import BaseTestCase


class SanicConfigTestCase(BaseTestCase):
    @BaseTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_global_config(self):
        from ddtrace import config
        from ddtrace.contrib.sanic import patch  # noqa: F401

        assert config.sanic.service == "mysvc"
