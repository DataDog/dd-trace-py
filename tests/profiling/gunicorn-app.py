import os
import threading
from typing import Callable


def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)


def app(environ: dict[str, str], start_response: Callable[[str, list[tuple[str, str]]], None]) -> list[bytes]:
    response_body = f"fib(35) is {fib(35)} at pid {os.getpid()} tid {threading.get_ident()}"

    response_body = response_body.encode("utf-8")

    status = "200 OK" if response_body else "404 Not Found"
    headers = [("Content-type", "text/plain")]
    start_response(status, headers)
    return [response_body]
