from datetime import datetime, timezone
import uuid
from typing import Dict, Any, List, Optional, Union
from ddtrace.internal.utils.formats import format_trace_id

def datetime_to_ns(dt: datetime) -> int:
    """Convert a datetime object to nanoseconds since the epoch."""
    return int(dt.timestamp() * 1e9)


def generate_span_id() -> str:
    """Generate a random span ID."""
    return str(uuid.uuid4())


def process_message_chunk(output_chunk):
    print(output_chunk)
    if output_chunk.type == "text":
        return {"role": "assistant", "content": output_chunk.text}
    elif output_chunk.type == "tool_reference":
        return {
            "tool_calls": [
                {
                    "name": output_chunk.title,
                    "arguments": {
                        "output": {
                            "title": output_chunk.title,
                            "url": output_chunk.url,
                            "source": output_chunk.source,
                        }
                    },
                    "type": output_chunk.type,
                }
            ]
        }
    elif output_chunk.type == "tool_file":
        return {
            "tool_calls": [
                {
                    "name": output_chunk.tool,
                    "arguments": {
                        "file_id": output_chunk.file_id,
                        "file_name": output_chunk.file_name,
                    },
                    "type": "function",
                }
            ]
        }
    elif output_chunk.type == "agent.handoff":
        return {
            "tool_calls": [
                {
                    "name": f"from_{output_chunk.previous_agent_name}_to_{output_chunk.next_agent_name}",
                    "type": "handoff",
                }
            ]
        }
    elif output_chunk.type == "function.call":
        return {
            "tool_calls": [
                {
                    "name": output_chunk.name,
                    "arguments": output_chunk.arguments,
                    "type": "function.call",
                    "tool_id": output_chunk.tool_call_id,
                }
            ]
        }
    elif output_chunk.type == "tool.execution":
        return {
            "tool_calls": [
                {
                    "name": output_chunk.name,
                    "type": "tool.execution",
                    "tool_id": output_chunk.id,
                }
            ]
        }
    else:
        raise ValueError(f"Unknown output chunk type: {output_chunk.type}")


def reconstruct_trace(
    start_time: int,
    end_time: int,
    trace_id,
    span_id,
    response,
    starting_agent_id: str = None,
    fetch_agent_info=None,
    input: str = None,
):
    outputs = [
        output
        for output in response.outputs
        if output.created_at and output.completed_at
    ]
    spans = []
    inferred_agent_boundaries = [
        {"agent_id": starting_agent_id, "start_time": start_time, "end_time": end_time}
    ]
    for output in outputs:
        if output.type == "agent.handoff":
            inferred_agent_boundaries[-1]["end_time"] = datetime_to_ns(
                output.completed_at
            )
            inferred_agent_boundaries[-1]["agent_id"] = output.previous_agent_id
            inferred_agent_boundaries.append(
                {
                    "agent_id": output.next_agent_id,
                    "start_time": datetime_to_ns(output.completed_at),
                    "end_time": None,
                }
            )
        if output.type == "message.output":
            inferred_agent_boundaries[-1]["end_time"] = datetime_to_ns(
                output.completed_at
            )

    if inferred_agent_boundaries[-1]["end_time"] is None:
        print(
            "No end time found for last agent boundary, dropping inferred agent boundary"
        )
        inferred_agent_boundaries.pop()

    if inferred_agent_boundaries[-1]["agent_id"] is None:
        for output in outputs:
            if output.type == "message.output":
                inferred_agent_boundaries[-1]["agent_id"] = output.agent_id
                break

    agent_spans = []
    for agent_boundary in inferred_agent_boundaries:
        if agent_boundary["end_time"] is None:
            continue
        agent_spans.append(
            create_inferred_agent_span(
                agent_boundary["start_time"],
                agent_boundary["end_time"],
                trace_id,
                span_id,
                response.conversation_id,
                "mistral-agent",
                agent_boundary["agent_id"],
                fetch_agent_info,
            )
        )

    """
    Infer the first LLM call per agent if it doesn't exist.
    Mistral doesn't return LLM call information if it results
    in a tool choice / hand-off, which is why we need to infer it here.
    """
    spans += agent_spans

    resp_idx = 0
    for agent in agent_spans:
        inferred_first_llm_call = [agent["start_ns"], None, {}]

        while resp_idx < len(outputs):
            if outputs[resp_idx].type in (
                "agent.handoff",
                "tool.execution",
                "function.call",
            ):
                inferred_first_llm_call[1] = datetime_to_ns(
                    outputs[resp_idx].created_at
                )
                inferred_first_llm_call[2] = outputs[resp_idx]
                resp_idx += 1
                break
            resp_idx += 1
        if inferred_first_llm_call[1] is not None:
            start, end, metadata = inferred_first_llm_call
            spans.append(
                create_inferred_llm_span(
                    start,
                    end,
                    trace_id,
                    agent["span_id"],
                    response.conversation_id,
                    "mistral-agent",
                    metadata,
                    parent_name=agent["name"],
                    input=input,
                )
            )
    """
    Now, we can create the `actual` spans from each output even
    of mistral, which **should** contain `created_at` and `completed_at`
    times.
    """
    for output in outputs:
        if output.type == "message.output":
            spans.append(
                _create_llm_span(
                    output,
                    trace_id,
                    agent_spans[-1]["span_id"],
                    response.conversation_id,
                    "mistral-agent",
                )
            )
        elif output.type == "function.call":
            # TODO: we need to add this function call
            # as output to the last llm span
            pass
        elif output.type == "agent.handoff":
            spans.append(
                _create_agent_handoff_span(
                    output,
                    trace_id,
                    agent_spans[0]["span_id"],
                    response.conversation_id,
                    "mistral-agent",
                )
            )
        elif output.type == "tool.execution":
            spans.append(
                _create_tool_execution_span(
                    output,
                    trace_id,
                    agent_spans[-1]["span_id"],
                    response.conversation_id,
                    "mistral-agent",
                )
            )

    spans.sort(key=lambda x: x["start_ns"])
    """
    Now we construct span links
    Given some (llm) -> (X # of tools) -> (llm) .. pattern, we want links between 
    all of these spans.
    """
    llm_span_ids = []
    tool_span_ids = []
    last_llm_span_idx_to_put_token_info_on = None
    for i, span in enumerate(spans):
        if "span_links" not in spans[i]:
            spans[i]["span_links"] = []
        if span["meta"]["span.kind"] == "llm":
            last_llm_span_idx_to_put_token_info_on = i
            llm_span_ids.append(span["span_id"])
            for tool_idx in tool_span_ids:
                spans[i]["span_links"].append(
                    {
                        "trace_id": trace_id,
                        "span_id": tool_idx,
                        "attributes": {
                            "from": "output",
                            "to": "input",
                        },
                    }
                )
            tool_span_ids = []
        elif span["meta"]["span.kind"] == "tool":
            tool_span_ids.append(span["span_id"])
            if llm_span_ids:
                last_llm_span_id = llm_span_ids[-1]
                spans[i]["span_links"].append(
                    {
                        "trace_id": trace_id,
                        "span_id": last_llm_span_id,
                        "attributes": {
                            "from": "output",
                            "to": "input",
                        },
                    }
                )

    spans[last_llm_span_idx_to_put_token_info_on]["usage"] = {
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    for span in spans:
        from ddtrace.llmobs import LLMObs
        LLMObs._instance._llmobs_span_writer.enqueue(span)
    return spans


def create_inferred_agent_span(
    start_time: int,
    end_time: int,
    trace_id,
    parent_id,
    session_id,
    ml_app,
    agent_id,
    fetch_agent_info,
):
    span_id = generate_span_id()
    meta = {
        "span.kind": "agent",
    }
    agent = fetch_agent_info(agent_id)

    def convert_tool(tool):
        if tool.type == "function":
            tool = tool.function
            return {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict_json_schema": tool.strict,
            }
        elif tool.type == "web_search":
            return {
                "name": "web_search",
            }
        elif tool.type == "code_interpreter":
            return {
                "name": "code_interpreter",
            }

    meta["metadata"] = {
        "agent_manifest": {
            "framework": "mistral",
            "name": agent.name + " (inferred)",
            "model": agent.model,
            "handoffs": agent.handoffs,
            "model_provider": "mistral",
            "instructions": agent.instructions,
            "tools": [convert_tool(tool) for tool in agent.tools],
            "model_settings": {
                "top_p": agent.completion_args.top_p,
                "max_tokens": agent.completion_args.max_tokens,
            },
        }
    }
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": agent.name + " (inferred)",
        "start_ns": start_time,
        "duration": end_time - start_time,
        "status": "ok",
        "meta": meta,
        "metrics": {},
        "_dd": {"span_id": span_id, "trace_id": trace_id},
        "session_id": session_id,
        "tags": ["ml_app:" + ml_app, "session_id:" + session_id],
    }


def create_inferred_llm_span(
    start_time: int,
    end_time: int,
    trace_id,
    parent_id,
    session_id,
    ml_app,
    output,
    parent_name,
    input: str = None,
):
    span_id = generate_span_id()
    meta = {
        "span.kind": "llm",
        "metadata": {
            "id": output.id,
        },
    }
    # Add model information if available
    meta["model_name"] = "mistral-large-latest"
    meta["model_provider"] = "mistral"
    meta["metadata"] = {"output": output.model_dump()}
    if input:
        meta["input"] = {"messages": [{"role": "user", "content": input}]}
    if output:
        meta["output"] = {"messages": [process_message_chunk(output)]}
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": parent_name.replace(" (inferred)", "") + " (inferred LLM call)",
        "start_ns": start_time,
        "duration": end_time - start_time,
        "status": "ok",
        "meta": meta,
        "metrics": {},
        "_dd": {"span_id": span_id, "trace_id": trace_id},
        "session_id": session_id,
        "tags": ["ml_app:" + ml_app, "session_id:" + session_id],
    }


def _create_llm_span(output, trace_id, parent_id, session_id, ml_app):
    """
    Create a single LLM span where:
    - All message outputs except the last one are collected as input messages
    - Only the last message output is treated as the output
    """
    span_id = generate_span_id()

    # Get timing from the entire sequence
    start_ns = (
        datetime_to_ns(output.created_at)
        if output.created_at
        else datetime_to_ns(datetime.now(timezone.utc))
    )
    end_ns = datetime_to_ns(output.completed_at) if output.completed_at else start_ns
    duration = end_ns - start_ns

    if isinstance(output.content, list):
        input_messages = []
        for chunk in output.content[1:]:
            input_messages.append(process_message_chunk(chunk))
        output_messages = [process_message_chunk(output.content[0])]
    else:
        input_messages = []
        output_messages = [{"role": "assistant", "content": output.content}]

    meta = {
        "span.kind": "llm",
        "metadata": {"id": output.id, "message_count": len(output.content)},
    }
    # Add input messages if there are any
    if input_messages:
        meta["input"] = {"messages": input_messages}

    # Add output message
    meta["output"] = {"messages": output_messages}

    # Add model information if available
    meta["model_name"] = output.model
    meta["model_provider"] = "mistral"

    # Add agent info if available
    if hasattr(output, "agent_id") and output.agent_id:
        meta["metadata"]["agent_id"] = output.agent_id

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": "mistral.llm.conversation",
        "start_ns": start_ns,
        "duration": duration,
        "status": "ok",
        "meta": meta,
        "metrics": {},
        "_dd": {"span_id": span_id, "trace_id": trace_id},
        "session_id": session_id,
        "tags": ["ml_app:" + ml_app, "session_id:" + session_id],
    }


def _create_tool_execution_span(output, trace_id, parent_id, session_id, ml_app):
    """Create a span event for tool.execution type."""
    span_id = generate_span_id()
    start_ns = (
        datetime_to_ns(output.created_at)
        if output.created_at
        else datetime_to_ns(datetime.now(timezone.utc))
    )
    end_ns = datetime_to_ns(output.completed_at) if output.completed_at else start_ns
    duration = end_ns - start_ns

    meta = {
        "span.kind": "tool",
        "metadata": {
            "id": output.id,
            "tool_name": output.name,
            "output_index": getattr(output, "output_index", 0),
        },
    }

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": output.name,
        "start_ns": start_ns,
        "duration": duration,
        "status": "ok",
        "meta": meta,
        "metrics": {},
        "_dd": {"span_id": span_id, "trace_id": trace_id},
        "session_id": session_id,
        "tags": ["ml_app:" + ml_app, f"tool:{output.name}", "session_id:" + session_id],
    }


def _create_agent_handoff_span(output, trace_id, parent_id, session_id, ml_app):
    """Create a span event for agent.handoff type."""
    span_id = generate_span_id()
    start_ns = (
        datetime_to_ns(output.created_at)
        if output.created_at
        else datetime_to_ns(datetime.now(timezone.utc))
    )
    end_ns = datetime_to_ns(output.completed_at) if output.completed_at else start_ns
    duration = end_ns - start_ns

    meta = {
        "span.kind": "tool",
        "metadata": {
            "id": output.id,
            "from_agent_id": output.previous_agent_id,
            "to_agent_id": output.next_agent_id,
        },
        "input": {
            "value": output.previous_agent_name,
        },
        "output": {
            "value": output.next_agent_name,
        },
    }

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": "mistral.agent.handoff",
        "start_ns": start_ns,
        "duration": duration,
        "status": "ok",
        "meta": meta,
        "metrics": {},
        "_dd": {"span_id": span_id, "trace_id": trace_id},
        "session_id": session_id,
        "tags": [
            "ml_app:" + ml_app,
            "session_id:" + session_id,
        ],
    }
