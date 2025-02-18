import asyncio


def test_basic_try_except_f(value):
    try:
        raise ValueError("auto caught error")
    except ValueError:
        value = 10
    return value


def test_basic_multiple_except_f(a, value):
    try:
        if a == 0:
            raise ValueError("auto value caught error")
        else:
            raise RuntimeError("auto caught error")
    except ValueError:
        value += 10
    except RuntimeError as _:
        value += 5
    return value


def test_handled_same_error_multiple_times_f(value):
    try:
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise e
    except Exception:
        value = 10

    return value


async def test_sync_error_f(value):
    task = asyncio.create_task(test_async_error_f(value))
    try:
        raise ValueError("this is a sync error")
    except ValueError:
        value += "<sync_error>"
    value += await task
    return value


async def test_async_error_f(value):
    await asyncio.sleep(1)
    try:
        raise ValueError("this is an async error")
    except ValueError:
        value += "<async_error>"
    return value


def test_reraise_handled_error_f(value):
    try:
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise RuntimeError(e)
    except RuntimeError:
        value = 10

    return value


def test_report_after_unhandled_without_raise_f(value):
    try:
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise RuntimeError(e)
    except RuntimeError:
        value = 10
    return value


module_user_code_string = """
def module_user_code():
    try:
        raise ValueError("module caught error")
    except ValueError:
        value = "<except_module_f>"
    return value
"""

main_user_code_string = """
def main_user_code(value):
    from user_module import module_user_code
    from tests.internal.error_reporting.module.submodule.sample_submodule_1 import submodule_1

    try:
        raise ValueError("auto caught error")
    except ValueError:
        value += "<except_f>"

    value += module_user_code()
    value += submodule_1()
    return value
"""
