import abc
import dataclasses
import time


def str_to_bool(_input):
    return _input in (True, "True", "true", "Yes", "yes", "Y", "y", "On", "on", "1", 1)


def _register(scenario_cls):
    """Registers a scenario for benchmarking."""

    import pyperf

    # This extends pyperf's runner by registering arguments for the scenario config
    def add_cmdline_args(cmd, args):
        for _field in dataclasses.fields(scenario_cls):
            if hasattr(args, _field.name):
                cmd.extend(("--{}".format(_field.name), str(getattr(args, _field.name))))

    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    cmd = runner.argparser

    for _field in dataclasses.fields(scenario_cls):
        cmd.add_argument("--{}".format(_field.name), type=_field.type if _field.type is not bool else str_to_bool)

    parsed_args = runner.parse_args()

    config_dict = {
        _field.name: getattr(parsed_args, _field.name)
        for _field in dataclasses.fields(scenario_cls)
        if hasattr(parsed_args, _field.name)
    }

    scenario = scenario_cls(**config_dict)

    runner.bench_time_func(scenario.scenario_name, scenario._pyperf)


@dataclasses.dataclass
class Scenario:
    """The base class for specifying a benchmark."""

    name: str

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        dataclasses.dataclass(cls)
        _register(cls)

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
            return dt  # noqa: B012
