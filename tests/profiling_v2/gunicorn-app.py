import os
import threading


def fib(n):
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)


def app(environ, start_response):
    response_body = "fib(30) is %d at pid %d tid %d" % (fib(30), os.getpid(), threading.get_ident())

    response_body = response_body.encode("utf-8")

    status = "200 OK" if response_body else "404 Not Found"
    headers = [("Content-type", "text/plain")]
    start_response(status, headers)
    return [response_body]
