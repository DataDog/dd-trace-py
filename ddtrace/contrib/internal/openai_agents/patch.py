import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.llmobs._integrations.openai_agents import count_tools_chars
from ddtrace.trace import tracer


log = get_logger(__name__)


config._add("openai_agents", {})


def get_version() -> str:
    from agents import version

    return getattr(version, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"agents": ">=0.0.2"}


OPENAI_AGENTS_VERSION = parse_version(get_version())


async def patched_run_single_turn(func, instance, args, kwargs):
    return await _patched_run_single_turn(func, instance, args, kwargs, agent_index=0)


async def patched_run_single_turn_streamed(func, instance, args, kwargs):
    return await _patched_run_single_turn(func, instance, args, kwargs, agent_index=1)


async def _patched_run_single_turn(func, instance, args, kwargs, agent_index=0):
    current_span = tracer.current_span()
    result = await func(*args, **kwargs)

    if current_span is None:
        log.debug("No current span available, skipping tag_agent_manifest")
        return result

    integration = agents._datadog_integration
    integration.tag_agent_manifest(current_span, args, kwargs, agent_index)

    # MLOB-7584: capture agent-side context categories (tools + handoffs) for the
    # context_delta payload. First call locks the first-agent identity. Subsequent calls
    # from the same agent update "last"; calls from a different agent (i.e. after a
    # handoff) are discarded — see ``record_agent_side`` in openai_agents.py. Emission
    # happens at on_trace_end in the processor.
    # AIDEV-NOTE: MLOB-7584 — handoffs are folded into the ``tools`` category because the
    # context_delta payload has no dedicated handoffs key. If one is added later, split
    # count_tools_chars back into two separate counts.
    agent = get_argument_value(args, kwargs, agent_index, "agent", True)
    if agent:
        integration.record_agent_side(
            current_span.trace_id,
            tools_chars=count_tools_chars(agent),
            agent_id=getattr(agent, "name", None),
        )

    return result


# AIDEV-NOTE: MLOB-7584 — module-level ``run_single_turn`` shim for agents >= ~0.10.
# In newer agents versions the per-turn function moved from ``AgentRunner._run_single_turn``
# (instance method) to ``agents.run_internal.run_loop.run_single_turn`` (module-level free
# function). The new signature takes ``bindings: AgentBindings`` as its first positional
# arg. ``AgentBindings`` exposes ``execution_agent`` (the agent actually running the turn)
# and ``public_agent`` (the caller-facing identity, which can differ during handoffs);
# we prefer execution_agent, fall back to public_agent, and finally to a legacy ``.agent``
# attribute for forward-compat if the upstream type ever exposes one. Reuses the existing
# manifest + agent-side capture by routing through a single agent-object code path.
# Both wrappers below route to the same inner ``_patched_run_single_turn_module``
# implementation. This is safe because the two wrapped functions sit at distinct
# call sites in the agents library: ``agents.run.run_single_turn`` is invoked
# only from ``Runner.run`` (the non-streamed entry point), and
# ``agents.run_internal.run_loop.run_single_turn_streamed`` is invoked only from
# ``Runner.run_streamed``. A single agent turn flows through exactly one — they
# cannot both fire for the same turn, so there is no double-record risk.
async def patched_run_single_turn_module(func, instance, args, kwargs):
    return await _patched_run_single_turn_module(func, instance, args, kwargs)


async def patched_run_single_turn_streamed_module(func, instance, args, kwargs):
    return await _patched_run_single_turn_module(func, instance, args, kwargs)


async def _patched_run_single_turn_module(func, instance, args, kwargs):
    current_span = tracer.current_span()
    result = await func(*args, **kwargs)

    if current_span is None:
        log.debug("No current span available, skipping tag_agent_manifest")
        return result

    # AIDEV-NOTE: MLOB-7584 — ``AgentBindings`` (agents >= ~0.10) carries
    # ``public_agent`` (caller-facing identity) and ``execution_agent`` (the agent
    # actually running the turn). They differ during handoffs. We tag with the
    # execution agent so the manifest reflects what's actually running and the
    # tools char count matches the active tool set for this turn.
    bindings = args[0] if args else kwargs.get("bindings")
    agent = None
    if bindings is not None:
        agent = (
            getattr(bindings, "execution_agent", None)
            or getattr(bindings, "public_agent", None)
            or getattr(bindings, "agent", None)
        )
    if agent is None:
        return result

    integration = agents._datadog_integration
    integration.tag_agent_manifest_from_agent(current_span, agent)
    integration.record_agent_side(
        current_span.trace_id,
        tools_chars=count_tools_chars(agent),
        agent_id=getattr(agent, "name", None),
    )

    return result


def _has_module_level_run_loop() -> bool:
    """Detect agents >= ~0.10 where run_single_turn is module-level."""
    try:
        from agents.run_internal import run_loop as _rl  # noqa: F401

        return hasattr(_rl, "run_single_turn") or hasattr(_rl, "run_single_turn_streamed")
    except ImportError:
        return False


# AIDEV-NOTE: MLOB-7584 — wrap targets for agents >= ~0.10 are *re-exported* into
# ``agents.run`` at module load time (see ``agents/run.py:84-95``). Wrapping
# ``agents.run_internal.run_loop.run_single_turn`` AFTER that import is too late —
# ``run.py`` already holds a stale reference to the unwrapped function and its
# call sites at run.py:1196 and :1252 invoke the unwrapped version. Wrapping at
# the call-site module (``agents.run``) is the only way the wrap takes effect.
# The streamed variant is called from within run_loop.py itself, so it must be
# wrapped on the run_loop module.
_MODULE_RUN_LOOP_WRAP_TARGETS: list[tuple[str, str, str]] = [
    # (module_path, attr_name, wrapper_name).
    # ``agents.run`` re-exports run_single_turn at module load time (see
    # agents/run.py:84-95). We MUST wrap on the call-site module — wrapping
    # ``run_internal.run_loop`` is too late because run.py already holds an unwrapped
    # reference. The streamed variant is called from within run_loop.py itself, so
    # wrapping on run_loop is correct for that path.
    ("agents.run", "run_single_turn", "patched_run_single_turn_module"),
    (
        "agents.run_internal.run_loop",
        "run_single_turn_streamed",
        "patched_run_single_turn_streamed_module",
    ),
]


def _resolve_module(path: str):
    import importlib

    return importlib.import_module(path)


def patch():
    """
    Patch the instrumented methods
    """
    if getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = True

    integration = OpenAIAgentsIntegration(integration_config=config.openai_agents)
    add_trace_processor(LLMObsTraceProcessor(integration))
    agents._datadog_integration = integration

    # AIDEV-NOTE: MLOB-7584 — version detection order matters. Newest first.
    # Each branch is hasattr-guarded so the patch silently no-ops if the upstream
    # internals change again, rather than crashing.
    if _has_module_level_run_loop():
        _wrappers_by_name = {
            "patched_run_single_turn_module": patched_run_single_turn_module,
            "patched_run_single_turn_streamed_module": patched_run_single_turn_streamed_module,
        }
        for module_path, attr_name, wrapper_name in _MODULE_RUN_LOOP_WRAP_TARGETS:
            try:
                mod = _resolve_module(module_path)
            except ImportError:
                continue
            if hasattr(mod, attr_name):
                wrap(mod, attr_name, _wrappers_by_name[wrapper_name])
    elif OPENAI_AGENTS_VERSION >= (0, 0, 19):
        if hasattr(agents.run.AgentRunner, "_run_single_turn"):
            wrap(agents.run.AgentRunner, "_run_single_turn", patched_run_single_turn)
        if hasattr(agents.run.AgentRunner, "_run_single_turn_streamed"):
            wrap(agents.run.AgentRunner, "_run_single_turn_streamed", patched_run_single_turn_streamed)
    else:
        wrap(agents.run.Runner, "_run_single_turn", patched_run_single_turn)
        wrap(agents.run.Runner, "_run_single_turn_streamed", patched_run_single_turn_streamed)


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = False

    if _has_module_level_run_loop():
        for module_path, attr_name, _ in _MODULE_RUN_LOOP_WRAP_TARGETS:
            try:
                mod = _resolve_module(module_path)
            except ImportError:
                continue
            if hasattr(mod, attr_name):
                unwrap(mod, attr_name)
    elif OPENAI_AGENTS_VERSION >= (0, 0, 19):
        if hasattr(agents.run.AgentRunner, "_run_single_turn"):
            unwrap(agents.run.AgentRunner, "_run_single_turn")
        if hasattr(agents.run.AgentRunner, "_run_single_turn_streamed"):
            unwrap(agents.run.AgentRunner, "_run_single_turn_streamed")
    else:
        unwrap(agents.run.Runner, "_run_single_turn")
        unwrap(agents.run.Runner, "_run_single_turn_streamed")
