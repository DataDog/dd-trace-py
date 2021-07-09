from typing import Any

CONFIG_KEY: str
REQUEST_CONTEXT_KEY: str
REQUEST_CONFIG_KEY: str
REQUEST_SPAN_KEY: str

async def trace_middleware(app: Any, handler: Any): ...
async def on_prepare(request: Any, response: Any) -> None: ...
def trace_app(app: Any, tracer: Any, service: str = ...) -> None: ...
