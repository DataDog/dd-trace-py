from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


async def _retrieve_context(instance):
    if instance is None:
        return
    try:
        # set flag to skip tracing during internal context retrieval
        instance._dd_internal_context_query = True
        await instance.query("/context")
        context_messages = []
        async for msg in instance.receive_response():
            context_messages.append(msg)
        return context_messages
    except Exception:
        log.warning("Error retrieving after context from claude_agent_sdk", exc_info=True)
    finally:
        if instance is not None:
            instance._dd_internal_context_query = False