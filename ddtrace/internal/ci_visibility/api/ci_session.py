from ddtrace.internal.ci_visibility.api.ci_base import CIVisibilityBase


class CIVisibilitySession(CIVisibilityBase):
    def __init__(self, session):
        self._session = session

        self.modules = {}

    def start(self):
        pass

    def finish(self:
        pass