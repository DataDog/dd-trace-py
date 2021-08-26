class MyException(Exception):
    pass


def job_add1(x):
    return x + 1


def job_fail():
    raise MyException("error")


class JobClass(object):
    def __call__(self, x):
        return x * 2

    def job_on_class(self, x):
        return x / 2
