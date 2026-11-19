# Keep the vulnerable application and its source locations unchanged.
from pygoat.settings import *  # noqa: F403


MIDDLEWARE = [*MIDDLEWARE, "allauth.account.middleware.AccountMiddleware"]  # noqa: F405
