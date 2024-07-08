from dataclasses import dataclass
from dataclasses import field

import bm
import utils


@dataclass
class DjangoSimpleParent:
    name: str
    tracer_enabled: bool = field(default_factory=bm.var_bool)
    profiler_enabled: bool = field(default_factory=bm.var_bool)
    appsec_enabled: bool = field(default_factory=bm.var_bool)
    iast_enabled: bool = field(default_factory=bm.var_bool)


class DjangoSimple(DjangoSimpleParent, bm.Scenario):
    def run(self):
        with utils.server(self) as get_response:

            def _(loops):
                for _ in range(loops):
                    get_response()

            yield _
