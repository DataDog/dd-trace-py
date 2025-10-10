import os


def in_ray_job():
    # type: () -> bool
    """Returns whether we are in a ray environemt.
    This is accomplished by checking if the _RAY_SUBMISSION_ID environment variable is defined
    which means a job has been submitted and is traced
    """
    return bool(os.environ.get("_RAY_SUBMISSION_ID", False))
