import abc
import time
import typing as t

import attr
import pyperf
import six
from contextlib import contextmanager


@attr.s
class Variant:
    name = attr.ib(type=str)
    spec = attr.ib(type=t.Dict[str, t.Union[int, str, bool]], factory=dict)


@attr.s
class Scenario(six.with_metaclass(abc.ABCMeta)):
    variant = attr.ib(type=Variant)
    name = attr.ib(type=str, init=False)
    spec = attr.ib(type=t.Dict[str, t.Union[t.Type[int], t.Type[str]]], init=False)

    @abc.abstractmethod
    @contextmanager
    def run_ctx(self):
        # type: (Variant) -> Callable[int, None]
        pass

    def perf(self, loops):
        with self.run_ctx(self.variant) as ctx:
            t0 = time.perf_counter()
            ctx(loops)
            dt = time.perf_counter() - t0
            return dt

    @property
    def name(self):
        # type: () -> str
        return "{}-{}".format(self.name, self.variant.name)


def run(scenario_cls):
    def add_cmdline_args(cmd, args):
        if args.variant:
            cmd.extend(("--variant", args.variant))
        for k in scenario_cls.spec.keys():
            if hasattr(args, k):
                cmd.extend(("--{}".format(k), str(getattr(args, k))))

    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    cmd = runner.argparser
    cmd.add_argument("--variant", type=str)

    for (k, v) in scenario_cls.spec.items():
        cmd.add_argument("--{}".format(k), type=v)

    args = runner.parse_args()

    variant = Variant(args.variant, spec={k: getattr(args, k) for k in scenario_cls.spec if hasattr(args, k)})

    scenario = scenario_cls(variant=variant)

    runner.bench_time_func(scenario.name, scenario.perf)
