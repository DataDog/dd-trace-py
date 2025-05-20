from tests.profiling.collector import pprof_utils


def check_pprof_file(
    filename,  # type: str
):
    profile = pprof_utils.parse_profile(filename)

    cpu_samples = pprof_utils.get_samples_with_value_type(profile, "cpu-samples")
    assert len(cpu_samples) >= 1
