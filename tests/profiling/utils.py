from tests.profiling.collector import pprof_utils


def check_pprof_file(
    filename,  # type: str
    sample_type="cpu-samples",
):
    profile = pprof_utils.parse_profile(filename)

    samples = pprof_utils.get_samples_with_value_type(profile, sample_type)
    assert len(samples) >= 1
