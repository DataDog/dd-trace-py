from agents import Agent
from agents import Handoff
from agents import (
    WebSearchTool,
    FileSearchTool,
    ComputerTool,
)

def create_agent_manifest(agent):
    manifest = {}
    manifest["framework"] = "OpenAI"

    if hasattr(agent, "name"):
        manifest["name"] = agent.name
    if hasattr(agent, "instructions"):
        manifest["instructions"] = agent.instructions
    if hasattr(agent, "handoff_description"):
        manifest["handoff_description"] = agent.handoff_description
    if hasattr(agent, "model"):
        model = agent.model
        manifest["model"] = model if isinstance(model, str) else getattr(model, "model", "")

    model_settings = extract_model_settings_from_agent(agent)
    if model_settings:
        manifest["model_settings"] = model_settings

    tools = extract_tools_from_agent(agent)
    if tools:
        manifest["tools"] = tools

    handoffs = extract_handoffs_from_agent(agent)
    if handoffs:
        manifest["handoffs"] = handoffs
    
    guardrails = extract_guardrails_from_agent(agent)
    if guardrails:
        manifest["guardrails"] = guardrails
        
    return manifest

def extract_model_settings_from_agent(agent):
    if not hasattr(agent, "model_settings"):
        return None
    
    # convert model_settings to dict if it's not already
    model_settings = agent.model_settings
    if type(model_settings) != dict:
        if hasattr(model_settings, "__dict__"):
            model_settings = model_settings.__dict__
        else:
            return None
    
    return make_json_compatible(model_settings)

def extract_tools_from_agent(agent):
    if not hasattr(agent, "tools"):
        return None
    
    tools = []
    for tool in agent.tools:
        tool_dict = {}
        if isinstance(tool, WebSearchTool):
            if hasattr(tool, "user_location"):
                tool_dict["user_location"] = tool.user_location
            if hasattr(tool, "search_context_size"):
                tool_dict["search_context_size"] = tool.search_context_size
        elif isinstance(tool, FileSearchTool):
            if hasattr(tool, "vector_store_ids"):
                tool_dict["vector_store_ids"] = tool.vector_store_ids
            if hasattr(tool, "max_num_results"):
                tool_dict["max_num_results"] = tool.max_num_results
            if hasattr(tool, "include_search_results"):
                tool_dict["include_search_results"] = tool.include_search_results
        elif isinstance(tool, ComputerTool):
            if hasattr(tool, "name"):
                tool_dict["name"] = tool.name
        else:
            if hasattr(tool, "name"):
                tool_dict["name"] = tool.name
            if hasattr(tool, "description"):
                tool_dict["description"] = tool.description
            if hasattr(tool, "strict_json_schema"):
                tool_dict["strict_json_schema"] = tool.strict_json_schema
            if hasattr(tool, "params_json_schema"):
                parameter_schema = tool.params_json_schema
                required_params = get_required_param_dict(parameter_schema.get("required", [])) 
                parameters = {}
                for param, schema in parameter_schema.get("properties", {}).items():
                    param_dict = {}
                    if "type" in schema:
                        param_dict["type"] = schema["type"]
                    if "title" in schema:
                        param_dict["title"] = schema["title"]
                    if param in required_params:
                        param_dict["required"] = True
                    parameters[param] = param_dict
                tool_dict["parameters"] = parameters
        tools.append(tool_dict)                     
    
    return tools

def extract_handoffs_from_agent(agent):
    if not hasattr(agent, "handoffs"):
        return None
    
    handoffs = []
    for handoff in agent.handoffs:
        handoff_dict = {}
        if isinstance(handoff, Agent):
            if hasattr(handoff, "handoff_description"):
                handoff_dict["handoff_description"] = handoff.handoff_description
            if hasattr(handoff, "name"):
                handoff_dict["agent_name"] = handoff.name
        elif isinstance(handoff, Handoff):
            if hasattr(handoff, "tool_name"):
                handoff_dict["tool_name"] = handoff.tool_name
            if hasattr(handoff, "tool_description"):
                handoff_dict["handoff_description"] = handoff.tool_description
            if hasattr(handoff, "agent_name"):
                handoff_dict["agent_name"] = handoff.agent_name
        if handoff_dict:
            handoffs.append(handoff_dict)

    return handoffs

def extract_guardrails_from_agent(agent):
    guardrails = []
    if hasattr(agent, "input_guardrails"):
        guardrails.extend([getattr(guardrail, "name", "") for guardrail in agent.input_guardrails])
    if hasattr(agent, "output_guardrails"):
        guardrails.extend([getattr(guardrail, "name", "") for guardrail in agent.output_guardrails])
    return guardrails

def get_required_param_dict(required_params):
    return {param: True for param in required_params}

def make_json_compatible(obj):
    if isinstance(obj, dict):
        return {str(k): make_json_compatible(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [make_json_compatible(v) for v in obj]
    elif isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    else:
        return str(obj)
