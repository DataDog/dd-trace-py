import django


if django.VERSION >= (2,):
    def user_is_authenticated(user):
        return user.is_authenticated
else:
    def user_is_authenticated(user):
        return user.is_authenticated()
