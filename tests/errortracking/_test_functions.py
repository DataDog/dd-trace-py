import asyncio


def test_basic_try_except_f(value):
    try:
        raise ValueError("auto caught error")
    except ValueError:
        value = 10
    return value


def test_basic_multiple_except_f(value):
    try:
        raise ValueError("auto value caught error")
    except ValueError:
        value += 10

    try:
        raise RuntimeError("auto caught error")
    except RuntimeError:
        value += 5
    return value


def test_handle_same_error_multiple_times_f(value):
    try:
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise e
    except Exception:
        value = 10

    return value


def test_handled_same_error_different_type_f(value):
    try:
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise RuntimeError(e)
    except RuntimeError:
        value = 10

    return value


def test_handled_then_raise_error_f(value):
    try:
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise e
    except Exception as e:
        raise e

def test_more_handled_than_collector_capacity_f(value):
    for i in range(101):
        try:
            raise ValueError("auto caught error")
        except ValueError:
            value += 1
    return value

def handled_in_parent_span_f(value, tracer):
    @tracer.wrap('parent_span')
    def parent_span(value):
        try:
            child_span()
        except ValueError:
            value += 1
        return value

    @tracer.wrap('child_span')
    def child_span():
        try:
            raise ValueError("auto caught error")
        except ValueError as e:
            raise e

    return parent_span(value)

def test_asyncio_error_f(value):
    return asyncio.run(test_sync_error_f(value))

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


module_user_code_string = """
def module_user_code():
    try:
        raise ValueError("module caught error")
    except ValueError:
        value = "<except_module_f>"
    return value
"""

submodule_1_string = """
def submodule_1_f():
    value = ""
    try:
        raise RuntimeError("<error_function_submodule_1>")
    except Exception:
        value += "<except_submodule_1>"
    return value
"""

submodule_2_string = """
def submodule_2_f():
    value = ""
    try:
        raise ValueError("<error_function_submodule_2>")
    except Exception:
        value += "<except_submodule_2>"
    return value
"""

main_user_code_string = """
def main_user_code(value):
    from user_module import module_user_code
    import numpy

    try:
        raise ValueError("auto caught error")
    except ValueError:
        value += "<except_f>"

    value += module_user_code()
    value += numpy.numpy_f()
    return value
"""
