from types import SimpleNamespace

from ddtrace.appsec._iast._langchain import _langchain_chatmodel_generate_after
from ddtrace.appsec._iast._langchain import _langchain_iast_taint_chunk
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted


def test_langchain_taints_dictionary_chunk_content(iast_context_defaults):
    message = SimpleNamespace(content={"answer": "safe", "index": 1})
    source = Source("prompt", "tainted", OriginType.PARAMETER)

    _langchain_iast_taint_chunk(source, message)

    assert is_pyobject_tainted(message.content["answer"])
    assert message.content["index"] == 1


def test_langchain_taints_dictionary_generation_content(iast_context_defaults):
    prompt = taint_pyobject("tainted", "prompt", "tainted", OriginType.PARAMETER)
    messages = [[SimpleNamespace(content=prompt)]]
    message = SimpleNamespace(content={"answer": "safe", "index": 1})
    completions = SimpleNamespace(generations=[[SimpleNamespace(message=message)]])

    _langchain_chatmodel_generate_after(messages, completions)

    assert is_pyobject_tainted(message.content["answer"])
    assert message.content["index"] == 1
