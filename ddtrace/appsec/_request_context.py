from typing import Optional
from typing import Text
from typing import Type

from ddtrace import Span
from ddtrace.internal import core


def in_context(context_key) -> bool:
    return core.get_item(context_key) is not None


class AppsecEnvironment:
    context_key = ""

    def __init__(self, span: Optional[Span] = None):
        self.root = not in_context(self.context_key)
        if span is None:
            self.span: Span = core.get_item("call")
        else:
            self.span = span

    def callbacks(self):
        raise NotImplementedError()


class Context:
    def __init__(self, context_key: Text, environment_class: Type[AppsecEnvironment]):
        self._env_class: Type[AppsecEnvironment] = environment_class
        self.context_key: Text = context_key

    def get(self):
        core.get_item(self.context_key)

    def in_context(self) -> bool:
        return core.get_item(self.context_key) is not None

    def finalize_asm_env(self, env: Type[AppsecEnvironment]) -> None:
        core.discard_local_item(self.context_key)
