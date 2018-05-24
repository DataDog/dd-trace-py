import opentracing


class Scope(opentracing.Scope):
    """"""

    __slots__ = ['_manager', '_span']

    def close(self):
        """"""
        if self._finish_on_exit:
            self._span.finish()

    def __enter__(self):
        """"""
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        """"""
        self.close()
