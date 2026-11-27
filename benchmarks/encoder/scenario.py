from collections.abc import Callable
from collections.abc import Generator

import bm
import utils


class Encoder(bm.Scenario):  # type: ignore[misc]
    ntraces: int
    nspans: int
    ntags: int
    ltags: int
    nmetrics: int
    nmetastructs: int
    lmetastructs: int
    encoding: str
    dd_origin: bool

    def run(self) -> Generator[Callable[[int], None], None, None]:
        encoder = utils.init_encoder(self.encoding)
        traces = utils.gen_traces(self)

        def _(loops: int) -> None:
            for _ in range(loops):
                for trace in traces:
                    encoder.put(trace)
                    encoder.encode()

        yield _
