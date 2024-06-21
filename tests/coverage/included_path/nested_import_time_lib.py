NESTED_CONSTANT = "nested imported constant"
USED_NESTED_CONSTANT = "used nested constant"


def compute_nested_constant():
    ret_val = "computed " + NESTED_CONSTANT
    return ret_val


NESTED_COMPUTED_CONSTANT = compute_nested_constant()
