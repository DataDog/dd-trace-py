"""Connection-local observers for Smithy's independently consumed duplex halves."""

from typing import Any
from typing import Optional

from wrapt import ObjectProxy

from ddtrace.contrib.internal.aws_sdk_bedrock_runtime._sonic import SonicState


class InputProxy(ObjectProxy):  # type: ignore[misc]  # wrapt has no typed proxy base.
    def __init__(self, wrapped: Any, state: SonicState) -> None:
        super().__init__(wrapped)
        self._self_state = state

    async def send(self, event: Any) -> Any:
        try:
            result = await self.__wrapped__.send(event)
        except BaseException:
            self._self_state.finish_error()
            raise
        # Only accepted bytes advance the provider's input sample clock.
        self._self_state.observe(event, outbound=True)
        return result

    async def close(self) -> Any:
        # Half-close is not response completion: the receiver must be allowed to drain.
        try:
            return await self.__wrapped__.close()
        except BaseException:
            self._self_state.finish_error()
            raise

    async def __aenter__(self) -> Any:
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        return await self.__wrapped__.__aexit__(*args)


class OutputProxy(ObjectProxy):  # type: ignore[misc]  # wrapt has no typed proxy base.
    def __init__(self, wrapped: Any, state: SonicState) -> None:
        super().__init__(wrapped)
        self._self_state = state

    async def receive(self) -> Any:
        try:
            event = await self.__wrapped__.receive()
        except StopAsyncIteration:
            self._self_state.finish()
            raise
        except BaseException:
            self._self_state.finish_error()
            raise
        if event is None:
            self._self_state.finish()
        else:
            self._self_state.observe(event)
        return event

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> Any:
        event = await self.receive()
        if event is None:
            raise StopAsyncIteration
        return event

    async def close(self) -> Any:
        try:
            return await self.__wrapped__.close()
        except BaseException:
            self._self_state.finish_error()
            raise
        finally:
            self._self_state.finish()

    async def __aenter__(self) -> Any:
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        try:
            return await self.__wrapped__.__aexit__(*args)
        except BaseException:
            self._self_state.finish_error()
            raise
        finally:
            if args and args[0] is not None:
                self._self_state.finish_error(args)
            else:
                self._self_state.finish()


class DuplexProxy(ObjectProxy):  # type: ignore[misc]  # wrapt has no typed proxy base.
    def __init__(self, wrapped: Any, state: SonicState) -> None:
        super().__init__(wrapped)
        self._self_state = state
        self._self_input = InputProxy(wrapped.input_stream, state)
        self._self_output: Optional[OutputProxy] = None
        self._wrap_output()

    @property
    def input_stream(self) -> InputProxy:
        return self._self_input

    @property
    def output_stream(self) -> Optional[OutputProxy]:
        self._wrap_output()
        return self._self_output

    def _wrap_output(self) -> None:
        receiver = self.__wrapped__.output_stream
        if receiver is not None and (self._self_output is None or self._self_output.__wrapped__ is not receiver):
            self._self_output = OutputProxy(receiver, self._self_state)

    async def await_output(self) -> tuple[Any, Optional[OutputProxy]]:
        try:
            response, _ = await self.__wrapped__.await_output()
        except BaseException:
            self._self_state.finish_error()
            raise
        return response, self.output_stream

    async def close(self) -> Any:
        try:
            return await self.__wrapped__.close()
        except BaseException:
            self._self_state.finish_error()
            raise
        finally:
            self._self_state.finish()

    async def __aenter__(self) -> Any:
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        try:
            return await self.__wrapped__.__aexit__(*args)
        except BaseException:
            self._self_state.finish_error()
            raise
        finally:
            if args and args[0] is not None:
                self._self_state.finish_error(args)
            else:
                self._self_state.finish()
