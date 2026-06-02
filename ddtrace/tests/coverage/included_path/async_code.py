import asyncio
import sys


async def get_line_number():
    try:
        raise Exception("this is line 7")
    except Exception:
        exception_type, exception, traceback = sys.exc_info()
        return traceback.tb_lineno


def call_async_function_and_report_line_number():
    return asyncio.run(get_line_number())
