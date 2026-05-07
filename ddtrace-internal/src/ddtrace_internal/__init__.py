"""Internal Datadog Python package (stub)."""

__version__ = "0.0.0"


def check_module_path(module, attr_path):
    """
    Helper function to safely check if a nested attribute path exists on a module.

    Args:
        module: The root module object
        attr_path: Dot-separated path to the attribute (e.g., "flows.llm_flows.functions")

    Returns:
        bool: True if the full path exists, False otherwise

    Example:
        check_module_path(adk, "flows.llm_flows.functions.__call_tool_async")
        check_module_path(adk, "agents.llm_agent.LlmAgent")
    """
    if not module:
        return False

    try:
        current = module
        for attr in attr_path.split("."):
            if not hasattr(current, attr):
                return False
            current = getattr(current, attr)

        return True
    except (ImportError, AttributeError):
        # Some modules may raise ImportError when accessing attributes that require
        # additional dependencies (e.g., ContainerCodeExecutor requiring Docker for google-adk and an extra pkg install)
        return False
