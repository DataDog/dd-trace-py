# -*- encoding: utf-8 -*-
import os
import threading
from typing import Callable


def app(environ: dict[str, str], start_response: Callable[[str, list[tuple[str, str]]], None]) -> list[bytes]:
    response_body = f"ok pid {os.getpid()} tid {threading.get_ident()}".encode("utf-8")
    start_response("200 OK", [("Content-type", "text/plain")])
    return [response_body]
