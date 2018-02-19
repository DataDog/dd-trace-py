from webhelpers import *  # noqa


class ExceptionWithCodeMethod(Exception):
    def __init__(self, message):
        super(ExceptionWithCodeMethod, self).__init__(message)

    def code():
        pass
