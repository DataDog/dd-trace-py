import os

from ddtrace.contrib.internal.ray.utils import get_dd_job_name


def test_get_dd_job_name():
    assert get_dd_job_name("job:frobnitzigate_idiosyncrasies,run:38") == "frobnitzigate_idiosyncrasies"
    assert get_dd_job_name("joe.schmoe-cf32445c3b2842958956ba6b6225ad") == "joe.schmoe-cf32445c3b2842958956ba6b6225ad"
    assert get_dd_job_name("mortar.clustering.pipeline") == "mortar.clustering.pipeline"
    os.environ["_RAY_JOB_NAME"] = "train.cool.model"
    assert get_dd_job_name("whatever") == "train.cool.model"
    del os.environ["_RAY_JOB_NAME"]
    assert get_dd_job_name() == "unspecified.ray.job"
    os.environ["_RAY_SUBMISSION_ID"] = "job:frobnitzigate_idiosyncrasies,run:38"
    assert get_dd_job_name() == "frobnitzigate_idiosyncrasies"
    os.environ["_RAY_SUBMISSION_ID"] = "whatever"
    assert get_dd_job_name() == "whatever"
    del os.environ["_RAY_SUBMISSION_ID"]
    assert get_dd_job_name() == "unspecified.ray.job"
