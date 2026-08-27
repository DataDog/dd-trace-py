import re

from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.test_fastapi import _Test_FastAPI_Base


class Test_FastAPI_NonSnapshot(_Test_FastAPI_Base, utils.Contrib_TestClass_For_Threats_NonSnapshot):
    ENDPOINT_DISCOVERY_EXPECTED_PATHS = {
        "/",
        "/asm/{param_int:int}/{param_str:str}",
        "/asm/",
        "/files/{file_path:path}",
        "/multi-param/{first}.{last}/",
        "/new_service/{service_name:str}",
        "/login/",
        "/login_sdk/",
        "/rasp/{endpoint:str}/",
    }

    @staticmethod
    def endpoint_path_to_uri(path: str) -> str:
        path = re.sub(r"\{[a-z_]+:int\}", "123", path)
        path = re.sub(r"\{[a-z_]+:str\}", "abczx", path)
        return path
