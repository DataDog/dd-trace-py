import abc
import cProfile
import dataclasses
import os
import time
import typing


def str_to_bool(_input):
    return _input in (True, "True", "true", "Yes", "yes", "Y", "y", "On", "on", "1", 1)


def _register(scenario_cls: typing.Type["Scenario"]) -> None:
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
        if _field.name == "cprofile_loops":
            continue

        cmd.add_argument("--{}".format(_field.name), type=_field.type if _field.type is not bool else str_to_bool)

    parsed_args = runner.parse_args()

    config_dict = {
        _field.name: getattr(parsed_args, _field.name)
        for _field in dataclasses.fields(scenario_cls)
        if hasattr(parsed_args, _field.name)
    }

    scenario = scenario_cls(**config_dict)

    # If requests, generate a cProfile pstats file
    if scenario._cprofile_loops and parsed_args.append:
        pstats_output = os.path.join(os.path.dirname(parsed_args.append), "{}.pstats".format(scenario.scenario_name))

        with cProfile.Profile() as pr:
            try:
                scenario._pyperf(scenario._cprofile_loops)
            finally:
                pr.dump_stats(pstats_output)

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

    @property
    def _cprofile_loops(self) -> int:
        """Returns the number of loops to run for generating cProfile warmup stats.

        This can be set in the scenario class as a class variable, "cprofile_loops", or defaults to 200.

        Setting to 0 will disable cProfile generation.

        DEV: We cannot define a default property on this baseclass, otherwise all sub-classes will raise that
          they have a non-default property defined after a default one
        """
        return getattr(self, "cprofile_loops", 200)

    @abc.abstractmethod
    def run(self) -> typing.Generator[typing.Callable[[int], None], None, None]:
        """Returns a context manager that yields a function to be run for performance testing."""
        pass

    def _pyperf(self, loops: int) -> float:
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
        return dt
