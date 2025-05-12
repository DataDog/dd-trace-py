from ddtrace.appsec._iast._metrics import _set_iast_error_metric
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.settings.asm import config as asm_config


def langchain_listen(core):
    if not asm_config._iast_enabled:
        return
    core.on("langchain.patch", _langchain_patch)
    core.on("langchain.unpatch", _langchain_unpatch)
    core.on("langchain.llm.generate.after", _langchain_llm_generate_after)
    core.on("langchain.llm.agenerate.after", _langchain_llm_generate_after)
    core.on("langchain.chatmodel.generate.after", _langchain_chatmodel_generate_after)
    core.on("langchain.chatmodel.agenerate.after", _langchain_chatmodel_generate_after)
    core.on("langchain.stream.chunk.callback", _langchain_stream_chunk_callback)


def _langchain_patch():
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
    for class_ in agent_output_parser_classes:
        wrap("langchain", class_ + ".format", _wrapper_prompt_template_format)
        wrap("langchain", class_ + ".aformat", _wrapper_prompt_template_aformat)


def _langchain_unpatch():
    if asm_config._iast_enabled:
        return
    try:
        import langchain_core
    except ImportError:
        return
    unwrap(langchain_core.prompts.prompt.PromptTemplate, "format")
    unwrap(langchain_core.prompts.prompt.PromptTemplate, "aformat")


def _langchain_llm_generate_after(prompts, completions):
    """
    Taints the output of an LLM call if its inputs are tainted.

    Range propagation does not make sense in LLMs. So we get the first source in inputs, if any,
    and taint the full output with that source.
    """
    if not asm_config._iast_enabled:
        return
    if not isinstance(prompts, (tuple, list)):
        return
    if not hasattr(completions, "generations"):
        return
    try:
        generations = completions.generations
        if not isinstance(generations, list):
            return

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
                if not hasattr(gen, "text"):
                    continue
                text = gen.text
                if not isinstance(text, str):
                    continue
                new_text = taint_pyobject(
                    pyobject=text,
                    source_name=source.name,
                    source_value=source.value,
                    source_origin=source.origin,
                )
                setattr(gen, "text", new_text)
    except Exception as e:
        from ddtrace.appsec._iast._metrics import _set_iast_error_metric

        _set_iast_error_metric("IAST propagation error. langchain _iast_taint_llm_output. {}".format(e))


def _langchain_chatmodel_generate_after(messages, completions):
    if not asm_config._iast_enabled:
        return
    if not isinstance(messages, (tuple, list)):
        return
    if len(messages) == 0:
        return
    if not hasattr(completions, "generations"):
        return
    try:
        generations = completions.generations
        if not isinstance(generations, list):
            return
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
                if hasattr(gen, "text"):
                    text = gen.text
                    if not isinstance(text, str):
                        continue
                    new_text = taint_pyobject(
                        pyobject=text,
                        source_name=source.name,
                        source_value=source.value,
                        source_origin=source.origin,
                    )
                    setattr(gen, "text", new_text)
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
                        setattr(message, "content", {k: _iast_taint_if_str(source, v) for k, v in message.items()})
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
        from ddtrace.appsec._iast._metrics import _set_iast_error_metric

        _set_iast_error_metric("IAST propagation error. langchain _iast_taint_llm_output. {}".format(e))


def _langchain_stream_chunk_callback(interface_type, args, kwargs):
    chat_messages = get_argument_value(args, kwargs, 0, "input", optional=True)
    if not chat_messages:
        return None
    source = _get_tainted_source_from_chat_prompt_value(chat_messages)
    if not source:
        return None
    return _create_taint_chunk_callback(source)


def _create_taint_chunk_callback(source):
    def _iast_chunk_taint(chunk):
        _iast_taint_chunk(source, chunk)

    return _iast_chunk_taint


def _get_tainted_source_from_chat_prompt_value(chat_prompt_value):
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


def _iast_taint_chunk(source, chunk):
    """
    Taints a chunk (type BaseMessageChunk, typically an AIMessageChunk) given a source.
    """
    # Relevant attributes to taint are:
    #  content: Union[str, list[Union[str, dict]]]
    #  additional_kwargs: dict
    if not asm_config._iast_enabled:
        return
    if not source:
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
        setattr(message, "content", {k: _iast_taint_if_str(source, v) for k, v in message.items()})
    if hasattr(message, "additional_kwargs"):
        additional_kwargs = message.additional_kwargs
        if isinstance(additional_kwargs, dict) and "function_call" in additional_kwargs:
            # OpenAI-style tool call, arguments are passed serialized in JSON.
            function_call = additional_kwargs["function_call"]
            if isinstance(function_call, dict) and "arguments" in function_call:
                arguments = function_call["arguments"]
                if isinstance(arguments, str):
                    function_call["arguments"] = _iast_taint_if_str(source, arguments)


def _iast_taint_if_str(source, obj):
    if not isinstance(obj, str):
        return obj
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

    return taint_pyobject(
        pyobject=obj,
        source_name=source.name,
        source_value=source.value,
        source_origin=source.origin,
    )


def _wrapper_prompt_template_format(func, instance, args, kwargs):
    """
    Propagate taint in PromptTemplate.format, from any input, to the output.
    """
    result = func(*args, **kwargs)
    return _propagate_prompt_template_format(kwargs, result)


async def _wrapper_prompt_template_aformat(func, instance, args, kwargs):
    """
    Propagate taint in PromptTemplate.aformat, from any input, to the output.
    """
    result = await func(*args, **kwargs)
    return _propagate_prompt_template_format(kwargs, result)


def _propagate_prompt_template_format(kwargs, result):
    try:
        if not asm_config.is_iast_request_enabled:
            return result

        for value in kwargs.values():
            ranges = get_tainted_ranges(value)
            if ranges:
                source = ranges[0].source
                return taint_pyobject(result, source.name, source.value, source.origin)
    except Exception as e:
        _set_iast_error_metric("IAST propagation error. langchain iast_propagate_prompt_template_format. {}".format(e))
    return result


def _wrapper_agentoutput_parse(func, instance, args, kwargs):
    result = func(*args, **kwargs)
    return _propagante_agentoutput_parse(args, kwargs, result)


async def _wrapper_agentoutput_aparse(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    return _propagante_agentoutput_parse(args, kwargs, result)


def _propagante_agentoutput_parse(args, kwargs, result):
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
        _set_iast_error_metric("IAST propagation error. langchain taint_parser_output. {}".format(e))
    return result
