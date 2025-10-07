DD_RAY_TRACE_CTX = "_dd_ray_trace_ctx"

# Environment variables
RAY_SUBMISSION_ID = "_RAY_SUBMISSION_ID"
RAY_JOB_NAME = "_RAY_JOB_NAME"

# Span tags pertaining to Ray
RAY_JOB_ID = "ray.job_id"
RAY_NODE_ID = "ray.node_id"
RAY_WORKER_ID = "ray.worker_id"
RAY_TASK_ID = "ray.task_id"
RAY_ACTOR_ID = "ray.actor_id"
RAY_SUBMISSION_ID_TAG = "ray.submission_id"
RAY_HOSTNAME = "ray.hostname"
RAY_ENTRYPOINT = "ray.entrypoint"

# Default job name if not set by the user
DEFAULT_JOB_NAME = "unnamed.ray.job"

# Component tag
RAY_COMPONENT = "ray"

# Task and job status values
RAY_STATUS_SUCCESS = "success"
RAY_STATUS_ERROR = "error"
RAY_STATUS_RUNNING = "RUNNING"
RAY_STATUS_FINISHED = "FINISHED"
RAY_STATUS_FAILED = "FAILED"

# Ray span tag names
RAY_TASK_ARGS = "ray.task.args"
RAY_TASK_KWARGS = "ray.task.kwargs"
RAY_TASK_STATUS = "ray.task.status"
RAY_TASK_SUBMIT_STATUS = "ray.task.submit_status"
RAY_ACTOR_METHOD_ARGS = "ray.actor_method.args"
RAY_ACTOR_METHOD_KWARGS = "ray.actor_method.kwargs"
RAY_JOB_STATUS = "ray.job.status"
RAY_JOB_SUBMIT_STATUS = "ray.job.submit_status"
RAY_JOB_MESSAGE = "ray.job.message"
RAY_WAIT_TIMEOUT = "ray.wait.timeout_s"
RAY_WAIT_NUM_RETURNS = "ray.wait.num_returns"
RAY_WAIT_FETCH_LOCAL = "ray.wait.fetch_local"
RAY_GET_VALUE_SIZE_BYTES = "ray.get.value_size_bytes"
RAY_PUT_VALUE_TYPE = "ray.put.value_type"
RAY_PUT_VALUE_SIZE_BYTES = "ray.put.value_size_bytes"
RAY_METADATA_PREFIX = "ray.job.metadata"

# Long-running span metric names
DD_PARTIAL_VERSION = "_dd.partial_version"
DD_WAS_LONG_RUNNING = "_dd.was_long_running"

# Special values
REDACTED_VALUE = "<redacted>"
REDACTED_PATH = "<redacted/path/to>"
