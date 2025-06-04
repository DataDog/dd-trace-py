from multiprocessing.pool import ThreadPool
import time

from importlib_metadata import version


_PORT = 8040

werkzeug_version = tuple(map(int, version("werkzeug").split(".")))
flask_version = tuple([int(v) for v in version("flask").split(".")])


def _multi_requests(client, url="/", debug_mode=False):
    if debug_mode:
        results = [
            _request(
                client,
            )
            for _ in range(10)
        ]
    else:
        pool = ThreadPool(processes=9)
        results_async = [pool.apply_async(_request, (client, url)) for _ in range(50)]
        results = [res.get() for res in results_async]

    return results


def _request_200(
    client,
    url="/",
    extra_validation=lambda response: response.content == b"OK_index",
    debug_mode=False,
    max_retries=40,
    sleep_time=1,
):
    """retry until it gets at least 2 successful checks"""
    time.sleep(sleep_time)
    previous = False
    for id_try in range(max_retries):
        results = _multi_requests(client, url, debug_mode)
        check = True
        for response in results:
            print(response.content)
            if response.status_code != 200 or not extra_validation(response):
                check = False
                break
        if check:
            if previous:
                return
            previous = True
        else:
            previous = False
        time.sleep(sleep_time * pow(8, id_try / max_retries))
    raise AssertionError("request_200 failed, max_retries=%d, sleep_time=%f" % (max_retries, sleep_time))


def _request(client, url="/"):
    response = client.get(url, headers={"X-Forwarded-For": "123.45.67.88"})
    return response
