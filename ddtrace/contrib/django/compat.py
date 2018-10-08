import django


if django.VERSION >= (1, 10, 1):
    def user_is_authenticated(user):
        # Explicit comparision due to the following bug
        # https://code.djangoproject.com/ticket/26988
        return user.is_authenticated == True  # noqa E712
else:
    def user_is_authenticated(user):
        return user.is_authenticated()
