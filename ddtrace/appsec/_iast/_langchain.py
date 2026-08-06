from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import TypeVar

from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled
from ddtrace.appsec._iast._logs import iast_error
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils import get_argument_value


class _EventCore(Protocol):
    def on(self, event_id: str, callback: Callable[..., object], name: object = None) -> None: ...


class _Generations(Protocol):
    generations: list[list[object]]


R = TypeVar("R")


def langchain_listen(core: _EventCore) -> None:
    if not asm_config._iast_enabled:
        return
    core.on("langchain.patch", _langchain_patch)
    core.on("langchain.unpatch", _langchain_unpatch)
    core.on("langchain.llm.generate.after", _langchain_llm_generate_after)
    core.on("langchain.llm.agenerate.after", _langchain_llm_generate_after)
    core.on("langchain.chatmodel.generate.after", _langchain_chatmodel_generate_after)
    core.on("langchain.chatmodel.agenerate.after", _langchain_chatmodel_generate_after)
    core.on("langchain.stream.chunk.callback", _langchain_stream_chunk_callback)


def _langchain_patch() -> None:
    """
    Patch langchain for IAST. MUST NOT be called directly, only as a callback
    from ddtrace.contrib.internal.langchain.patch import wrap
    """
    if not asm_config._iast_enabled:
        return

    wrap("langchain_core", "prompts.prompt.PromptTemplate.format", _wrapper_prompt_template_format)
    wrap("langchain_core", "prompts.prompt.PromptTemplate.aformat", _wrapper_prompt_template_aformat)

    agent_output_parser_classes = (
        "agents.chat.output_parser.ChatOutputParser",
        "agents.conversational.output_parser.ConvoOutputParser",
        "agents.conversational_chat.output_parser.ConvoOutputParser",
        "agents.mrkl.output_parser.MRKLOutputParser",
        "agents.output_parsers.json.JSONAgentOutputParser",
        "agents.output_parsers.openai_functions.OpenAIFunctionsAgentOutputParser",
        "agents.output_parsers.react_json_single_input.ReActJsonSingleInputOutputParser",
        "agents.output_parsers.react_single_input.ReActSingleInputOutputParser",
        "agents.output_parsers.self_ask.SelfAskOutputParser",
        "agents.output_parsers.xml.XMLAgentOutputParser",
        "agents.react.output_parser.ReActOutputParser",
        "agents.self_ask_with_search.output_parser.SelfAskOutputParser",
        "agents.structured_chat.output_parser.StructuredChatOutputParser",
    )

    # Check which package contains agents module (langchain 0.1 vs langchain-community 0.2+)
    agents_package = None
    try:
        import langchain.agents  # noqa: F401

        agents_package = "langchain"
    except ImportError:
        try:
            import langchain_community.agents  # noqa: F401

            agents_package = "langchain_community"
        except ImportError:
            pass  # No agents module available

    if agents_package:
        for class_ in agent_output_parser_classes:
            # Only wrap if the class and methods exist
            try:
                # Check if the class exists and has the methods before wrapping
                import importlib

                module = importlib.import_module(agents_package)
                class_obj = module
                for part in class_.split("."):
                    class_obj = getattr(class_obj, part)
                if hasattr(class_obj, "format"):
                    wrap(agents_package, class_ + ".format", _wrapper_agentoutput_parse)
                if hasattr(class_obj, "aformat"):
                    wrap(agents_package, class_ + ".aformat", _wrapper_agentoutput_aparse)
            except (ImportError, AttributeError):
                continue  # Class or method doesn't exist, skip it


def _langchain_unpatch() -> None:
    if asm_config._iast_enabled:
        return
    try:
        import langchain_core
    except ImportError:
        return
    unwrap(langchain_core.prompts.prompt.PromptTemplate, "format")
    unwrap(langchain_core.prompts.prompt.PromptTemplate, "aformat")


def _langchain_llm_generate_after(prompts: object, completions: _Generations) -> None:
    """
    Taints the output of an LLM call if its inputs are tainted.

    Range propagation does not make sense in LLMs. So we get the first source in inputs, if any,
    and taint the full output with that source.
    """
    if not asm_config._iast_enabled:
        return
    if not isinstance(prompts, (tuple, list)):
        return
    try:
        generations = completions.generations

        source = None
        for prompt in prompts:
            if not isinstance(prompt, str):
                continue
            tainted_ranges = get_tainted_ranges(prompt)
            if tainted_ranges:
                source = tainted_ranges[0].source
                break
        if not source:
            return
        for gens in generations:
            for gen in gens:
                text_attr, text = _get_text_attribute_for_generation(gen)
                if not text_attr or not text:
                    continue
                new_text = taint_pyobject(
                    pyobject=text,
                    source_name=source.name,
                    source_value=source.value,
                    source_origin=source.origin,
                )
                setattr(gen, text_attr, new_text)
    except Exception as e:
        iast_error("propagation::source::langchain _langchain_llm_generate_after", exc=e)


def _langchain_chatmodel_generate_after(messages: object, completions: _Generations) -> None:
    if not asm_config._iast_enabled:
        return
    if not isinstance(messages, (tuple, list)):
        return
    if len(messages) == 0:
        return
    try:
        generations = completions.generations
        if len(generations) == 0:
            return

        source = None
        for msgs in messages:
            if not isinstance(msgs, list):
                continue
            for msg in msgs:
                if not hasattr(msg, "content"):
                    continue
                tainted_ranges = get_tainted_ranges(msg.content)
                if tainted_ranges:
                    source = tainted_ranges[0].source
                    break
            else:
                continue
            break
        if not source:
            return

        for gens in generations:
            for gen in gens:
                text_attr, text = _get_text_attribute_for_generation(gen)
                if text_attr and text:
                    new_text = taint_pyobject(
                        pyobject=text,
                        source_name=source.name,
                        source_value=source.value,
                        source_origin=source.origin,
                    )
                    setattr(gen, text_attr, new_text)
                if hasattr(gen, "message"):
                    message = gen.message
                    if not hasattr(message, "content"):
                        continue
                    content = message.content
                    if isinstance(content, str):
                        setattr(message, "content", _iast_taint_if_str(source, content))
                    elif isinstance(content, list):
                        setattr(message, "content", [_iast_taint_if_str(source, c) for c in content])
                    elif isinstance(content, dict):
                        setattr(message, "content", {k: _iast_taint_if_str(source, v) for k, v in content.items()})
                    if hasattr(message, "additional_kwargs"):
                        additional_kwargs = message.additional_kwargs
                        if isinstance(additional_kwargs, dict) and "function_call" in additional_kwargs:
                            # OpenAI-style tool call, arguments are passed serialized in JSON.
                            function_call = additional_kwargs["function_call"]
                            if isinstance(function_call, dict) and "arguments" in function_call:
                                arguments = function_call["arguments"]
                                if isinstance(arguments, str):
                                    function_call["arguments"] = _iast_taint_if_str(source, arguments)
    except Exception as e:
        iast_error("propagation::source::langchain _langchain_chatmodel_generate_after", exc=e)


def _langchain_stream_chunk_callback(
    interface_type: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> Optional[Callable[[object], None]]:
    chat_messages = get_argument_value(args, kwargs, 0, "input", optional=True)
    if not chat_messages:
        return None
    source = _get_tainted_source_from_chat_prompt_value(chat_messages)
    if not source:
        return None
    return _create_taint_chunk_callback(source)


def _create_taint_chunk_callback(source: Source) -> Callable[[object], None]:
    def _iast_chunk_taint(chunk: object) -> None:
        try:
            _langchain_iast_taint_chunk(source, chunk)
        except Exception as e:
            iast_error("propagation::source::langchain _langchain_iast_taint_chunk", exc=e)

    return _iast_chunk_taint


def _get_tainted_source_from_chat_prompt_value(chat_prompt_value: object) -> Optional[Source]:
    if not asm_config._iast_enabled:
        return None
    if not hasattr(chat_prompt_value, "messages"):
        return None
    messages = chat_prompt_value.messages
    if not isinstance(messages, (tuple, list)):
        return None

    for message in messages:
        if not hasattr(message, "content"):
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        tainted_ranges = get_tainted_ranges(content)
        if tainted_ranges:
            return tainted_ranges[0].source
    return None


def _get_text_attribute_for_generation(gen: object) -> tuple[Optional[str], Optional[str]]:
    text_attr = None
    if hasattr(gen, "_text"):
        # langchain-core 0.3.60+ uses _text attribute (and text is a property)
        # See https://github.com/langchain-ai/langchain/pull/31238
        text_attr = "_text"
    elif hasattr(gen, "text"):
        # Previous version use just text attribute.
        text_attr = "text"
    else:
        return None, None
    text = getattr(gen, text_attr)
    if not isinstance(text, str):
        return None, None
    return text_attr, text


def _langchain_iast_taint_chunk(source: Source, chunk: object) -> None:
    """
    Taints a chunk (type BaseMessageChunk, typically an AIMessageChunk) given a source.
    """
    # Relevant attributes to taint are:
    #  content: Union[str, list[Union[str, dict]]]
    #  additional_kwargs: dict
    if not asm_config._iast_enabled:
        return
    message = chunk
    if not hasattr(message, "content"):
        return
    content = message.content
    if isinstance(content, str):
        setattr(message, "content", _iast_taint_if_str(source, content))
    elif isinstance(content, list):
        setattr(message, "content", [_iast_taint_if_str(source, c) for c in content])
    elif isinstance(content, dict):
        setattr(message, "content", {k: _iast_taint_if_str(source, v) for k, v in content.items()})
    if hasattr(message, "additional_kwargs"):
        additional_kwargs = message.additional_kwargs
        if isinstance(additional_kwargs, dict) and "function_call" in additional_kwargs:
            # OpenAI-style tool call, arguments are passed serialized in JSON.
            function_call = additional_kwargs["function_call"]
            if isinstance(function_call, dict) and "arguments" in function_call:
                arguments = function_call["arguments"]
                if isinstance(arguments, str):
                    function_call["arguments"] = _iast_taint_if_str(source, arguments)


def _iast_taint_if_str(source: Source, obj: object) -> object:
    if not isinstance(obj, str):
        return obj
    return taint_pyobject(
        pyobject=obj,
        source_name=source.name,
        source_value=source.value,
        source_origin=source.origin,
    )


def _wrapper_prompt_template_format(
    func: Callable[..., R], instance: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> R:
    """
    Propagate taint in PromptTemplate.format, from any input, to the output.
    """
    result = func(*args, **kwargs)
    return _propagate_prompt_template_format(kwargs, result)


async def _wrapper_prompt_template_aformat(
    func: Callable[..., Awaitable[R]], instance: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> R:
    """
    Propagate taint in PromptTemplate.aformat, from any input, to the output.
    """
    result = await func(*args, **kwargs)
    return _propagate_prompt_template_format(kwargs, result)


def _propagate_prompt_template_format(kwargs: dict[str, object], result: R) -> R:
    try:
        if not is_iast_request_enabled():
            return result

        for value in kwargs.values():
            ranges = get_tainted_ranges(value)
            if ranges:
                source = ranges[0].source
                return taint_pyobject(result, source.name, source.value, source.origin)
    except Exception as e:
        iast_error("propagation::source::langchain iast_propagate_prompt_template_format", exc=e)
    return result


def _wrapper_agentoutput_parse(
    func: Callable[..., R], instance: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> R:
    result = func(*args, **kwargs)
    return _propagante_agentoutput_parse(args, kwargs, result)


async def _wrapper_agentoutput_aparse(
    func: Callable[..., Awaitable[R]], instance: object, args: tuple[object, ...], kwargs: dict[str, object]
) -> R:
    result = await func(*args, **kwargs)
    return _propagante_agentoutput_parse(args, kwargs, result)


def _propagante_agentoutput_parse(args: tuple[object, ...], kwargs: dict[str, object], result: R) -> R:
    try:
        try:
            from langchain_core.agents import AgentAction
            from langchain_core.agents import AgentFinish
        except ImportError:
            from langchain.agents import AgentAction
            from langchain.agents import AgentFinish
        ranges = get_tainted_ranges(args[0])
        if ranges:
            source = ranges[0].source
            if isinstance(result, AgentAction):
                result.tool_input = taint_pyobject(result.tool_input, source.name, source.value, source.origin)
            elif isinstance(result, AgentFinish) and "output" in result.return_values:
                values = result.return_values
                values["output"] = taint_pyobject(values["output"], source.name, source.value, source.origin)
    except Exception as e:
        iast_error("propagation::source::langchain taint_parser_output", exc=e)
    return result
