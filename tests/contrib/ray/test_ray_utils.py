from ddtrace.contrib.internal.ray.utils import get_dd_job_name


def test_get_dd_job_name():
    assert get_dd_job_name("raysubmit_VFFkXZzz5CGiiJqx") == "raysubmit"
    assert get_dd_job_name("joe.schmoe-cf32445c3b2842958956ba6b6225adf523") == "joe.schmoe"
    assert get_dd_job_name("mortar.clustering.pipeline") == "mortar.clustering.pipeline"
    assert get_dd_job_name("") == "unspecified.ray.job"
