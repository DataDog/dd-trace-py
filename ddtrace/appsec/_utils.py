# this module must not load any other unsafe appsec module directly

import logging
import typing
from typing import Any
from typing import Optional
import uuid

from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal._unpatched import unpatched_json_loads
from ddtrace.internal.compat import to_unicode
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import _get_blocked_template  # noqa:F401
from ddtrace.internal.utils.http import parse_form_multipart  # noqa:F401
from ddtrace.internal.utils.http import parse_form_params  # noqa:F401
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

_TRUNC_STRING_LENGTH = 1
_TRUNC_CONTAINER_DEPTH = 4
_TRUNC_CONTAINER_SIZE = 2


class _observator:
    def __init__(self):
        self.string_length: Optional[int] = None
        self.container_size: Optional[int] = None
        self.container_depth: Optional[int] = None

    def set_string_length(self, length: int):
        if self.string_length is None:
            self.string_length = length
        else:
            self.string_length = max(self.string_length, length)

    def set_container_size(self, size: int):
        if self.container_size is None:
            self.container_size = size
        else:
            self.container_size = max(self.container_size, size)

    def set_container_depth(self, depth: int):
        if self.container_depth is None:
            self.container_depth = depth
        else:
            self.container_depth = max(self.container_depth, depth)


def parse_response_body(raw_body):
    import xmltodict

    from ddtrace.appsec import _asm_request_context
    from ddtrace.contrib.internal.trace_utils import _get_header_value_case_insensitive

    if not raw_body:
        return

    if isinstance(raw_body, dict):
        return raw_body

    headers = _asm_request_context.get_waf_address(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES)
    if not headers:
        return
    content_type = _get_header_value_case_insensitive(
        {to_unicode(k): to_unicode(v) for k, v in dict(headers).items()},
        "content-type",
    )
    if not content_type:
        return

    def access_body(bd):
        if isinstance(bd, list) and isinstance(bd[0], (str, bytes)):
            bd = bd[0][:0].join(bd)
        if getattr(bd, "decode", False):
            bd = bd.decode("UTF-8", errors="ignore")
        if len(bd) >= API_SECURITY.MAX_PAYLOAD_SIZE:
            raise ValueError("response body larger than 16MB")
        return bd

    req_body = None
    try:
        # TODO handle charset
        if "json" in content_type:
            req_body = unpatched_json_loads(access_body(raw_body))
        elif "xml" in content_type:
            req_body = xmltodict.parse(access_body(raw_body))
        else:
            return
    except Exception:
        log.debug("Failed to parse response body", exc_info=True)
    else:
        return req_body


def _hash_user_id(user_id: str) -> str:
    import hashlib

    return f"anon_{hashlib.sha256(user_id.encode()).hexdigest()[:32]}"


def _safe_userid(user_id):
    try:
        _ = int(user_id)
        return user_id
    except ValueError:
        try:
            _ = uuid.UUID(user_id)
            return user_id
        except ValueError:
            pass

    return None


class _UserInfoRetriever:
    def __init__(self, user):
        self.user = user
        self.possible_user_id_fields = ["pk", "id", "uid", "userid", "user_id", "PK", "ID", "UID", "USERID"]
        self.possible_login_fields = ["username", "user", "login", "USERNAME", "USER", "LOGIN"]
        self.possible_email_fields = ["email", "mail", "address", "EMAIL", "MAIL", "ADDRESS"]
        self.possible_name_fields = [
            "name",
            "fullname",
            "full_name",
            "first_name",
            "NAME",
            "FULLNAME",
            "FULL_NAME",
            "FIRST_NAME",
        ]

    def find_in_user_model(self, possible_fields: typing.Sequence[str]) -> typing.Optional[str]:
        for field in possible_fields:
            value = getattr(self.user, field, None)
            if value is not None:
                return value

        return None  # explicit to make clear it has a meaning

    def get_userid(self):
        user_login = getattr(self.user, asm_config._user_model_login_field, None)
        if user_login is not None:
            return user_login

        user_login = self.find_in_user_model(self.possible_user_id_fields)
        return user_login

    def get_username(self):
        username = getattr(self.user, asm_config._user_model_name_field, None)
        if username is not None:
            return username

        if hasattr(self.user, "get_username"):
            try:
                return self.user.get_username()
            except Exception:
                log.debug("User model get_username member produced an exception: ", exc_info=True)

        return self.find_in_user_model(self.possible_login_fields)

    def get_user_email(self):
        email = getattr(self.user, asm_config._user_model_email_field, None)
        if email is not None:
            return email

        return self.find_in_user_model(self.possible_email_fields)

    def get_name(self):
        name = getattr(self.user, asm_config._user_model_name_field, None)
        if name is not None:
            return name

        return self.find_in_user_model(self.possible_name_fields)

    def get_user_info(self, login=False, email=False, name=False):
        """
        In safe mode, try to get the user id from the user object.
        In extended mode, try to also get the username (which will be the returned user_id),
        email and name.
        """
        user_extra_info = {}

        user_id = self.get_userid()
        if user_id is None:
            return None, {}

        if login:
            user_extra_info["login"] = self.get_username()
        if email:
            user_extra_info["email"] = self.get_user_email()
        if name:
            user_extra_info["name"] = self.get_name()
        return user_id, user_extra_info


def has_triggers(span) -> bool:
    if asm_config._use_metastruct_for_triggers:
        return (span.get_struct_tag(APPSEC.STRUCT) or {}).get("triggers", None) is not None
    return span.get_tag(APPSEC.JSON) is not None


def get_triggers(span) -> Any:
    import json

    if asm_config._use_metastruct_for_triggers:
        return (span.get_struct_tag(APPSEC.STRUCT) or {}).get("triggers", None)
    json_payload = span.get_tag(APPSEC.JSON)
    if json_payload:
        try:
            return json.loads(json_payload).get("triggers", None)
        except Exception:
            log.debug("Failed to parse triggers", exc_info=True)
    return None


def add_context_log(logger: logging.Logger, msg: str, offset: int = 0) -> str:
    filename, line_number, function_name, _stack_info = logger.findCaller(False, 3 + offset)
    return f"{msg}[{filename}, line {line_number}, in {function_name}]"
