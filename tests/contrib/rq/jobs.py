class MyException(Exception):
    pass


def job_add1(x):
    return x + 1


def job_fail():
    raise MyException("error")


class JobClass:
    def __call__(self, x):
        return x * 2

    def job_on_class(self, x):
        return x / 2


def job_forward(queue_name, x):
    import redis
    import rq

    from tests.contrib.config import REDIS_CONFIG

    rq.Queue(queue_name, connection=redis.Redis(port=REDIS_CONFIG["port"])).enqueue(job_add1, x)
    return x


def job_fail_once(key):
    import redis

    from tests.contrib.config import REDIS_CONFIG

    if redis.Redis(port=REDIS_CONFIG["port"]).incr(key) == 1:
        raise MyException("first attempt")
    return key
