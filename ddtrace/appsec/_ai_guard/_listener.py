from functools import partial

from ddtrace.appsec._ai_guard._langchain import _langchain_chatmodel_generate_before
from ddtrace.appsec._ai_guard._langchain import _langchain_chatmodel_stream_before
from ddtrace.appsec._ai_guard._langchain import _langchain_llm_generate_before
from ddtrace.appsec._ai_guard._langchain import _langchain_llm_stream_before
from ddtrace.appsec._ai_guard._langchain import _langchain_patch
from ddtrace.appsec._ai_guard._langchain import _langchain_unpatch
from ddtrace.appsec._ai_guard._openai import _openai_chat_completion_after
from ddtrace.appsec._ai_guard._openai import _openai_chat_completion_before
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.internal import core


def ai_guard_listen():
    client = new_ai_guard_client()
    _langchain_listen(client)
    _openai_listen(client)


def _langchain_listen(client: AIGuardClient):
    core.on("langchain.patch", partial(_langchain_patch, client))
    core.on("langchain.unpatch", _langchain_unpatch)

    core.on("langchain.chatmodel.generate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.agenerate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.stream.before", partial(_langchain_chatmodel_stream_before, client))

    core.on("langchain.llm.generate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.agenerate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.stream.before", partial(_langchain_llm_stream_before, client))


def _openai_listen(client: AIGuardClient):
    core.on("openai.chat.completions.create.before", partial(_openai_chat_completion_before, client))
    core.on("openai.chat.completions.create.after", partial(_openai_chat_completion_after, client))
