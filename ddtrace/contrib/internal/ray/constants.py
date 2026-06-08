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
RAY_NAMESPACE = "ray.namespace"
RAY_GCS_ADDRESS = "ray.gcs_address"
RAY_DASHBOARD_URL = "ray.dashboard_url"
RAY_VERSION = "ray.version"

# Default job name if not set by the user
DEFAULT_JOB_NAME = "unnamed.ray.job"

# Task and job status values
RAY_STATUS_SUCCESS = "success"
RAY_STATUS_ERROR = "error"
RAY_STATUS_RUNNING = "RUNNING"
RAY_STATUS_FINISHED = "FINISHED"
RAY_STATUS_FAILED = "FAILED"

# Ray span tag names
RAY_TASK_ARGS = "ray.task.args"
RAY_TASK_KWARGS = "ray.task.kwargs"
RAY_TASK_SUBMIT_STATUS = "ray.task.submit_status"
RAY_ACTOR_METHOD_ARGS = "ray.actor_method.args"
RAY_ACTOR_METHOD_KWARGS = "ray.actor_method.kwargs"
RAY_ACTOR_METHOD_SUBMIT_STATUS = "ray.actor_method.submit_status"
RAY_JOB_STATUS = "ray.job.status"
RAY_JOB_SUBMIT_STATUS = "ray.job.submit_status"
RAY_JOB_MESSAGE = "ray.job.message"
RAY_JOB_START_TIME_MS = "ray.job.start_time_ms"
RAY_JOB_END_TIME_MS = "ray.job.end_time_ms"
RAY_JOB_DRIVER_NODE_ID = "ray.job.driver_node_id"
RAY_JOB_DRIVER_AGENT_HTTP_ADDRESS = "ray.job.driver_agent_http_address"
RAY_JOB_HAS_RUNTIME_ENV = "ray.job.has_runtime_env"
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

# Task submission scheduling-hint tags (from _remote(...) kwargs)
RAY_TASK_FUNCTION_MODULE = "ray.task.function_module"
RAY_TASK_FUNCTION_QUALNAME = "ray.task.function_qualname"
RAY_TASK_NUM_CPUS = "ray.task.num_cpus"
RAY_TASK_NUM_GPUS = "ray.task.num_gpus"
RAY_TASK_NUM_RETURNS = "ray.task.num_returns"
RAY_TASK_MAX_RETRIES = "ray.task.max_retries"
RAY_TASK_ACCELERATOR_TYPE = "ray.task.accelerator_type"
RAY_TASK_SCHEDULING_STRATEGY = "ray.task.scheduling_strategy"
RAY_TASK_RESOURCES_PREFIX = "ray.task.resources."

# Actor-method identity tags
RAY_ACTOR_CLASS_NAME = "ray.actor.class_name"
RAY_ACTOR_MODULE_NAME = "ray.actor.module_name"
RAY_ACTOR_METHOD_NAME = "ray.actor.method_name"


# Ray serve related constants

RAY_APP_NAME = "ray.serve.app_name"
RAY_DEPLOYMENT_ARGS = "ray.serve.deployment.args"
RAY_DEPLOYMENT_KWARGS = "ray.serve.deployment.kwargs"

# This constant is defined here as it is used in ray core integration and
# we do not want ray core to be dependent of ray serve.
RAY_SERVE_REPLICA_METHOD_DENYLIST = {
    "record_routing_stats",
    "check_health",
    "is_allocated",
    "initialize_and_get_metadata",
}