import asyncio


async def async_function(my_string_1, my_string_2):
    my_string_1 += my_string_2
    _ = await asyncio.sleep(0.1)
    return my_string_1


def no_async_function(my_string_1, my_string_2):
    my_string_1 += my_string_2

    return my_string_1


async def yield_function(string_1):
    string_1 = "a" + string_1
    yield string_1
    string_1 = "b" + string_1
    yield string_1


async def async_yield_function(string_1):
    async for v in yield_function(string_1):
        yield v
