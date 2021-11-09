import abc
import time

import attr
import pyperf
import six

from ._to_bool import to_bool


var = attr.ib


def var_bool(*args, **kwargs):
    return attr.ib(*args, **kwargs, converter=to_bool)


def _register(scenario_cls):
    """Registers a scenario for benchmarking."""
    # This extends pyperf's runner by registering arguments for the scenario config
    def add_cmdline_args(cmd, args):
        for field in attr.fields(scenario_cls):
            if hasattr(args, field.name):
                cmd.extend(("--{}".format(field.name), str(getattr(args, field.name))))

    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    cmd = runner.argparser

    for field in attr.fields(scenario_cls):
        cmd.add_argument("--{}".format(field.name), type=field.type)

    parsed_args = runner.parse_args()

    config_dict = {
        field.name: getattr(parsed_args, field.name)
        for field in attr.fields(scenario_cls)
        if hasattr(parsed_args, field.name)
    }
    scenario = scenario_cls(**config_dict)

    runner.bench_time_func(scenario.scenario_name, scenario._pyperf)


class ScenarioMeta(abc.ABCMeta):
    def __init__(cls, name, bases, _dict):
        super(ScenarioMeta, cls).__init__(name, bases, _dict)

        # Make sure every sub-class is wrapped by `attr.s`
        cls = attr.s()(cls)

        # Do not register the base Scenario class
        # DEV: We cannot compare `cls` to `Scenario` since it doesn't exist yet
        if cls.__module__ != __name__:
            _register(cls)


class Scenario(six.with_metaclass(ScenarioMeta)):
    """The base class for specifying a benchmark."""

    name = attr.ib(type=str)

    @property
    def scenario_name(self):
        return "{}-{}".format(self.__class__.__name__.lower(), self.name)

    @abc.abstractmethod
    def run(self):
        """Returns a context manager that yields a function to be run for performance testing."""
        pass

    def _pyperf(self, loops):
        rungen = self.run()
        run = next(rungen)
        t0 = time.perf_counter()
        run(loops)
        dt = time.perf_counter() - t0
        try:
            # perform any teardown
            next(rungen)
        except StopIteration:
            pass
        finally:
            return dt
