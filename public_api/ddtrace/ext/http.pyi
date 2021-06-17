from typing import Any

URL: str
METHOD: str
STATUS_CODE: str
STATUS_MSG: str
QUERY_STRING: str
RETRIES_REMAIN: str
VERSION: str
TEMPLATE: str

def normalize_status_code(code: Any): ...
