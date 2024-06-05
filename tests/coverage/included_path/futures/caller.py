from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import ThreadPoolExecutor
import multiprocessing


def call_add_fork():
    multiprocessing.set_start_method("fork", force=True)

    from .fork import add

    with ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(add(), 3, 6)
        future.result()


def call_add_forkserver():
    multiprocessing.set_start_method("forkserver", force=True)
    from .forkserver import add

    with ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(add(), 3, 6)
        future.result()


def call_add_spawn():
    multiprocessing.set_start_method("spawn", force=True)

    from .spawn import add

    with ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(add(), 3, 6)
        future.result()


def call_add_thread():
    from .thread import add

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(add, 3, 6)
        future.result()
