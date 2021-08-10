import abc
import time

import attr
import pyperf
import six


def register(cls):
    """Decorator for scenario class that registers the scenario to be run."""
    wrapped_cls = attr.s(cls)
    _register(wrapped_cls)
    return wrapped_cls


var = attr.ib


@attr.s
class Scenario(six.with_metaclass(abc.ABCMeta)):
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
