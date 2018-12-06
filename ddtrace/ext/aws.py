from itertools import chain

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
    will parse t
    """
    index = 0
    response = []
    for arg in args:
        if arg and args_name[index] in traced_args_list:
            response += [(args_name[index], arg)]
        index += 1
    return response


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
    combined = []
    for e in args:
        if isinstance(e, list):
            combined.extend(e)
        else:
            combined.append(e)

    return combined

REGION = "aws.region"
AGENT = "aws.agent"
OPERATION = "aws.operation"
