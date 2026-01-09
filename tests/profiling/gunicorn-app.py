import os
import threading
from typing import Callable
from typing import Dict
from typing import List
from typing import Tuple


def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)


def app(environ: Dict[str, str], start_response: Callable[[str, List[Tuple[str, str]]], None]) -> List[bytes]:
    response_body = f"fib(30) is {fib(30)} at pid {os.getpid()} tid {threading.get_ident()}"

    response_body = response_body.encode("utf-8")

    status = "200 OK" if response_body else "404 Not Found"
    headers = [("Content-type", "text/plain")]
    start_response(status, headers)
    return [response_body]
