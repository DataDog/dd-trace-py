from ddtrace.contrib.internal.ray.utils import get_dd_job_name_from_entrypoint
from ddtrace.contrib.internal.ray.utils import get_dd_job_name_from_submission_id


def test_get_dd_job_name_from_submission_id():
    assert (
        get_dd_job_name_from_submission_id("job:frobnitzigate_idiosyncrasies,run:38") == "frobnitzigate_idiosyncrasies"
    )
    assert get_dd_job_name_from_submission_id("joe.schmoe-cf32445c3b2842958956ba6b6225ad") is None
    assert get_dd_job_name_from_submission_id("") is None


def test_get_dd_job_name_from_entrypoint():
    assert get_dd_job_name_from_entrypoint("python hello.py") == "hello"
    assert get_dd_job_name_from_entrypoint("python3 hello.py") == "hello"
    assert get_dd_job_name_from_entrypoint("/Users/bits/.pyenv/shims/python3 woof.py") == "woof"
    assert get_dd_job_name_from_entrypoint("python3 woof.py --breed mutt") == "woof"
    assert get_dd_job_name_from_entrypoint("perl meow.pl") is None