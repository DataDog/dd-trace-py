BLACKLIST_ENDPOINT = ["kms", "sts"]


def is_blacklist(endpoint_name):
    """Protecting the args sent to kms, sts to avoid security leaks
    if kms disabled test_kms_client in test/contrib/botocore  will fail
    if sts disabled test_sts_client in test/contrib/boto contrib will fail
    """
    return endpoint_name in BLACKLIST_ENDPOINT


def unpacking_args(args, args_name, traced_args_list):
    """
    @params:
        args: tuple of args sent to a patched function
        args_name: tuple containing the names of all the args that can be sent
        traced_args_list: list of names of the args we want to trace
    Returns a list of (arg name, arg) of the args we want to trace
    The number of args being variable from one call to another, this function
    will parse it"""
    index = 0
    response = []
    for arg in args:
        if arg and args_name[index] in traced_args_list:
            response += [(args_name[index], arg)]
        index += 1
    return response

def truncate_arg_value(value, max_len=1024):
    """Truncate values which are bytes and greater than `max_len`.
    Useful for parameters like 'Body' in `put_object` operations.
    """
    if isinstance(value, bytes) and len(value) > max_len:
        return b'...'

    return value


def flatten_args(args):
    """Filter operation arguments based on list of arguments to be traced
    """

    # unnest structure for argument values that are not simple/atomic
    def _unnest(t):
        key, value = t
        if isinstance(value, dict):
            return [
                ('{}.{}'.format(key, inner_key), inner_value)
                for (inner_key, inner_value) in value.items()
            ]
        else:
            return (key, value)

    # append elements from unnesting
    unnested = [_unnest(t) for t in args]
    combined = []
    for e in unnested:
        if isinstance(e, list):
            combined.extend(e)
        else:
            combined.append(e)

    return combined


def add_span_arg_tags(span, endpoint_name, args, args_names, args_traced):
    # Adding the args in TRACED_ARGS if exist to the span
    if not is_blacklist(endpoint_name):
        operation_args = unpacking_args(args, args_names, args_traced)
        for (key, value) in flatten_args(operation_args):
            span.set_tag(key, truncate_arg_value(value))


REGION = "aws.region"
AGENT = "aws.agent"
OPERATION = "aws.operation"
