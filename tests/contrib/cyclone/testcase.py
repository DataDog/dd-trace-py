from twisted.trial import unittest
from .client import Client


class CycloneTestCase(unittest.TestCase, object):
    client_impl = Client
    app_builder = None

    def __init__(self, *args, **kwargs):
        """
        Create a test case for a cyclone app.

        The ``app_builder`` param should be a function that returns a
        cyclone.web.Application instance will all the appropriate handlers
        loaded etc.

        For most use cases this should be as simple as creating a function
        that returns you application instead of just declaring it in a file
        somewhere.
        """
        app_builder = None
        if "app_builder" in kwargs:
            app_builder = kwargs.pop("app_builder")
        if not app_builder and not self.app_builder:
            raise ValueError(
                "You need to either pass an app_builder named param to "
                "__init__ or set the app_builder attribute on your test case. "
                "it should be a callable that returns an app instance for "
                "your app. The Application class for your project may work."
            )
        super(CycloneTestCase, self).__init__(*args, **kwargs)
        builder = app_builder or self.app_builder
        self._app = builder()
        self.client = self.client_impl(self._app)
