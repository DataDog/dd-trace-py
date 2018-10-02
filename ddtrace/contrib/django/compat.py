import django


if django.VERSION >= (1, 10):
    def user_is_authenticated(user):
        return user.is_authenticated
else:
    def user_is_authenticated(user):
        return user.is_authenticated()
