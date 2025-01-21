from ddtrace.contrib.internal.redis_utils import determine_row_count
from ddtrace.contrib.internal.redis_utils import stringify_cache_args


format_command_args = stringify_cache_args


def determine_row_count(redis_command, span, result):  # noqa: F811
    determine_row_count(redis_command=redis_command, result=result)
