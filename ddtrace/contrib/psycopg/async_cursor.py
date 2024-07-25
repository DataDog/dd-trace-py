from typing import Optional
from typing import TypeVar

from ddtrace.contrib import dbapi_async
from ddtrace.contrib.psycopg.cursor import Psycopg3TracedCursor


Row = TypeVar("Row", covariant=True)

class Psycopg3TracedAsyncCursor(Psycopg3TracedCursor, dbapi_async.TracedAsyncCursor):
    def __init__(self, cursor, pin, cfg, *args, **kwargs):
        super(Psycopg3TracedAsyncCursor, self).__init__(cursor, pin, cfg)

    async def __aenter__(self):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        await self.__wrapped__.__aenter__()

        # and finally, yield the traced cursor.
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        return await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    def __aiter__(self):
        return self

    async def __anext__(self) -> Row:
        await self._fetch_pipeline()
        self._check_result_for_fetch()

        def load(pos: int) -> Row | None:
            return self._tx.load_row(pos, self._make_row)

        row = load(self._pos)
        if row is None:
            raise StopAsyncIteration

        self._pos += 1
        return row

    async def _fetch_pipeline(self) -> None:
        if (self._execmany_returning is not False
            and not self.pgresult
            and self._conn._pipeline):
            async with self._conn.lock:
                await self._conn.wait(self._conn._pipeline._fetch_gen(flush=True))

    def _fetch_row(self, pos: int) -> Optional[Row]:
        return self._tx.load_row(pos, self._make_row)


class Psycopg3FetchTracedAsyncCursor(Psycopg3TracedAsyncCursor, dbapi_async.FetchTracedAsyncCursor):
    """Psycopg3FetchTracedAsyncCursor for psycopg"""
