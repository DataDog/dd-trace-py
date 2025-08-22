from ddtrace.appsec._ai_guard._langchain import langchain_listen
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.internal import core


def ai_guard_listen(ai_guard: AIGuardClient):
    langchain_listen(core, ai_guard)
