from ddtrace.appsec._ai_guard._langchain import _langchain_patch, _langchain_unpatch, \
    _langchain_chatmodel_generate_before, _langchain_llm_generate_before
from ddtrace.internal import core


def ai_guard_listen():
    _langchain_listen()


def _langchain_listen():
    core.on("langchain.patch", _langchain_patch)
    core.on("langchain.unpatch", _langchain_unpatch)

    core.on("langchain.chatmodel.generate.before", _langchain_chatmodel_generate_before)
    core.on("langchain.chatmodel.agenerate.before", _langchain_chatmodel_generate_before)

    core.on("langchain.llm.generate.before", _langchain_llm_generate_before)
    core.on("langchain.llm.agenerate.before", _langchain_llm_generate_before)