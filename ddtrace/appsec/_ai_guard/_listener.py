from collections.abc import Mapping
from functools import partial
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.span import Span
from ddtrace.appsec._ai_guard._langchain import _langchain_chatmodel_generate_before
from ddtrace.appsec._ai_guard._langchain import _langchain_chatmodel_stream_before
from ddtrace.appsec._ai_guard._langchain import _langchain_llm_generate_before
from ddtrace.appsec._ai_guard._langchain import _langchain_llm_stream_before
from ddtrace.appsec._ai_guard._langchain import _langchain_patch
from ddtrace.appsec._ai_guard._langchain import _langchain_unpatch
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.contrib.internal.trace_utils import _get_request_header_client_ip
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.settings.asm import ai_guard_config


def ai_guard_listen():
    client = new_ai_guard_client()
    _langchain_listen(client)
    core.on("set_http_meta_for_asm", _on_set_http_meta_for_ai_guard)


def _langchain_listen(client: AIGuardClient):
    core.on("langchain.patch", partial(_langchain_patch, client))
    core.on("langchain.unpatch", _langchain_unpatch)

    core.on("langchain.chatmodel.generate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.agenerate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.stream.before", partial(_langchain_chatmodel_stream_before, client))

    core.on("langchain.llm.generate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.agenerate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.stream.before", partial(_langchain_llm_stream_before, client))


def _on_set_http_meta_for_ai_guard(
    span: Span,
    request_ip: Optional[str],
    raw_uri: Optional[str],
    route: Optional[str],
    method: Optional[str],
    request_headers: Optional[Mapping[str, str]],
    request_cookies: Optional[dict[str, str]],
    parsed_query: Optional[Mapping[str, Any]],
    request_path_params: Optional[Mapping[str, Any]],
    request_body: Any,
    status_code: Optional[Union[int, str]],
    response_headers: Optional[Mapping[str, str]],
    response_cookies: Optional[Mapping[str, str]],
    peer_ip: Optional[str] = None,
    headers_are_case_sensitive: bool = False,
) -> None:
    # Stash the candidate client IP so it can be applied to the service-entry span
    # only if an ai_guard span is actually created during the request. Restricted to
    # inbound server (WEB) spans so outbound HTTP client spans can't overwrite the key
    # with forwarded-IP headers from downstream calls.
    # https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6523551943
    if not ai_guard_config._ai_guard_enabled:
        return
    if span.span_type != SpanTypes.WEB:
        return
    candidate_ip = _get_request_header_client_ip(request_headers, peer_ip, headers_are_case_sensitive) or peer_ip
    if candidate_ip:
        core.set_item(AI_GUARD.CLIENT_IP_CORE_KEY, candidate_ip)
