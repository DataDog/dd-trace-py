import pytest

from tests.utils import override_global_config


class Interface:
    def __init__(self, name, framework, client):
        self.name = name
        self.framework = framework
        self.client = client


class Contrib_TestClass_For_Threats:
    @pytest.fixture
    def interface(self) -> Interface:
        raise NotImplementedError

    def test_healthcheck(self, interface: Interface, root_span):
        if interface.name == "fastapi":
            raise pytest.skip("fastapi does not have a healthcheck endpoint")
        with override_global_config(dict(_asm_enabled=True)):
            response = interface.client.get("/")
            assert response.status_code == 200, "healthcheck failed"
            assert response.data == b"ok ASM"
            from ddtrace.settings.asm import config as asm_config

            assert asm_config._asm_enabled
            assert root_span().get_tag("http.status_code") == "200"
