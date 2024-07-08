from dataclasses import dataclass

import bm
import utils


@dataclass
class DjangoSimpleParent:
    name: str
    tracer_enabled: bool
    profiler_enabled: bool
    appsec_enabled: bool
    iast_enabled: bool


class DjangoSimple(DjangoSimpleParent, bm.Scenario):
    def run(self):
        with utils.server(self) as get_response:

            def _(loops):
                for _ in range(loops):
                    get_response()

            yield _
