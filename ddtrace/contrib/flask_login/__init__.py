"""
The ``flask_login`` integration implements appsec automatic user login events
when ``DD_APPSEC_ENABLED=1``. This will automatically fill the following tags
when a user tries to log in using ``flask_login`` as an authentication plugin:

- ``appsec.events.users.login.success.track``
- ``appsec.events.users.login.failure.track``
- ``appsec.events.users.login.success.[email|login|username]``

Note that, by default, this will be enabled if ``DD_APPSEC_ENABLED=1`` with
``DD_APPSEC_AUTOMATIC_USER_EVENTS_TRACKING`` set to ``safe`` which will store the user's
``id`` but not the username or email. Check the configuration docs to see how to disable this feature entirely,
or set it to extended mode which would also store the username and email or customize the id, email and name
fields to adapt them to your custom ``User`` model.

Also, since ``flask_login`` is a "roll your own" kind of authentication system, in your main login function, where you
check the user password (usually with ``check_password_hash``) you must manually call
``track_user_login_failure_event(tracer, user_id, exists)`` to store the correct tags for authentication failure. As
a helper, you can call ``flask_login.login_user`` with a user object with a ``get_id()`` returning ``-1`` to
automatically set the tags for a login failure where the user doesn't exist.


Enabling
~~~~~~~~

This integration is enabled automatically when using ``DD_APPSEC_ENABLED=1`. Use
``DD_APPSEC_AUTOMATIC_USER_EVENTS_TRACKING=disabled`` to explicitly disable it.
"""
from ...internal.utils.importlib import require_modules


required_modules = ["flask_login"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "get_version",
            "patch",
            "unpatch",
        ]
