try:
    from . import _keyser_soze  # noqa: F401

    verbal_kint_is_keyser_soze = True
except ImportError:
    verbal_kint_is_keyser_soze = False


def func():
    VARIABLE_TO_FORCE_AST_PATCHINT = "a" + "b"
    return VARIABLE_TO_FORCE_AST_PATCHINT
