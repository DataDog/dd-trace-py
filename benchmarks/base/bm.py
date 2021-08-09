import abc
from contextlib import contextmanager
import time
import typing as t

import attr
import pyperf
import six


@attr.s
class Scenario(six.with_metaclass(abc.ABCMeta)):
    """The base class for specifying a benchmark.

    To add a scenario, inherit from from this ``Scenario`` class and:
    - add an inner subclass of ``bm.Scenario.Config`` with the attributes to be found in the ``config.yaml`` for the scenario.
    - implement ``run_ctx`` function to return a context manager for executing loops.

    For example::

      import attr
      import bm


      class MyScenario(bm.Scenario):
          @attr.s
          class Config(bm.Scenario.Config):
             size = attr.ib(type=int)


          @contextmanager
          def run_ctx(self):
              # perform setup
              size = self.config.size

              def _(loops):
                  for _ in range(loops):
                      2 ** size

              yield _

              # perform teardown
    """

    @attr.s(repr_ns="Scenario")
    class Config:
        name = attr.ib(type=str)

    _name = attr.ib(type=str, init=False)
    config = attr.ib(type=t.Dict[str, Config])

    @property
    def name(self):
        return "{}-{}".format(self.__class__.__name__.lower(), self.config.name)

    @abc.abstractmethod
    @contextmanager
    def run_ctx(self):
        """Returns a context manager that yields a function to be run for performance testing."""
        pass

    def _run(self, loops):
        with self.run_ctx() as ctx:
            t0 = time.perf_counter()
            ctx(loops)
            dt = time.perf_counter() - t0
            return dt


def register(scenario_cls):
    """Registers a scenario for benchmarking."""
    # This extends pyperf's runner by registering arguments for the scenario config
    def add_cmdline_args(cmd, args):
        for field in attr.fields(scenario_cls.Config):
            if hasattr(args, field.name):
                cmd.extend(("--{}".format(field.name), str(getattr(args, field.name))))

    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    cmd = runner.argparser

    for field in attr.fields(scenario_cls.Config):
        cmd.add_argument("--{}".format(field.name), type=field.type)

    parsed_args = runner.parse_args()

    config_dict = {
        field.name: getattr(parsed_args, field.name)
        for field in attr.fields(scenario_cls.Config)
        if hasattr(parsed_args, field.name)
    }
    config = scenario_cls.Config(**config_dict)
    scenario = scenario_cls(config=config)

    runner.bench_time_func(scenario.name, scenario._run)
