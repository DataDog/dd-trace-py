BLACKLIST_ENDPOINT = [
"kms",
"sts"
]


def is_blacklist(endpoint_name):
    """Protecting the args sent to kms, sts to avoid security leaks
    if kms disabled test_kms_client in test/contrib/botocore  will fail
    if sts disabled test_sts_client in test/contrib/boto contrib will fail
    """
    if endpoint_name in BLACKLIST_ENDPOINT:
        return True
    else:
        return False


def unpacking_args(args, args_name, traced_args_list):
    index = 0
    response = []
    for arg in args:
        if arg and args_name[index] in traced_args_list:
            response += [(args_name[index], arg)]
        index += 1
    return response
