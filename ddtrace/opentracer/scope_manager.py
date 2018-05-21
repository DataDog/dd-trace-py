from opentracing import ScopeManager as OpenTracingScopeManager


class ScopeManager(OpenTracingScopeManager):
    """"""

    def __init__(self):
        pass

    def activate(self, span, finish_on_close):
        """"""
        pass

    @property
    def active(self):
        """"""
        pass
