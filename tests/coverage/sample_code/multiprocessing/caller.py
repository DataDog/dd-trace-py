import multiprocessing


def call_fork():
    multiprocessing.set_start_method("fork", force=True)
    from multiprocessing import Process

    from .fork import add

    p = Process(target=add, args=(1, 2))
    p.start()
    p.join()


def call_forkserver():
    multiprocessing.set_start_method("forkserver", force=True)
    from multiprocessing import Process

    from .forkserver import add

    p = Process(target=add, args=(1, 2))
    p.start()
    p.join()


def call_spawn():
    multiprocessing.set_start_method("spawn", force=True)
    from multiprocessing import Process

    from .spawn import add

    p = Process(target=add, args=(1, 2))
    p.start()
    p.join()
