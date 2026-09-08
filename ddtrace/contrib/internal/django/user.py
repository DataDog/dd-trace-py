from typing import Any
from typing import Optional

from ddtrace.appsec._utils import _UserInfoRetriever
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)


class _DjangoUserInfoRetriever(_UserInfoRetriever):
    def __init__(self, user: object, credentials: Optional[dict[str, Any]] = None) -> None:
        super(_DjangoUserInfoRetriever, self).__init__(user)

        self.credentials = credentials if credentials else {}
        if self.credentials and not user:
            self._try_load_user()

    def _try_load_user(self):
        self.user_model = None

        try:
            from django.contrib.auth import get_user_model
        except ImportError:
            log.debug("user_exist: Could not import Django get_user_model", exc_info=True)
            return

        try:
            self.user_model = get_user_model()
            if not self.user_model:
                return
        except Exception:
            log.debug("user_exist: Could not get the user model", exc_info=True)
            return

        login_field = asm_config._user_model_login_field
        login_field_value = self.credentials.get(login_field, None) if login_field else None

        if not login_field or not login_field_value:
            # Try to get the username from the credentials
            for possible_login_field in self.possible_login_fields:
                if possible_login_field in self.credentials:
                    login_field = possible_login_field
                    login_field_value = self.credentials[login_field]
                    break
            else:
                # Could not get what the login field, so we can't check if the user exists
                log.debug("try_load_user_model: could not get the login field from the credentials")
                return

        try:
            self.user = self.user_model.objects.get(**{login_field: login_field_value})
        except self.user_model.DoesNotExist:
            log.debug("try_load_user_model: could not load user model", exc_info=True)

    def user_exists(self) -> bool:
        return self.user is not None

    def get_username(self) -> Optional[str]:
        username_field = getattr(type(self.user), "USERNAME_FIELD", None)
        if username_field is not None and not asm_config._user_model_name_field:
            username = getattr(self.user, username_field, None)
            return str(username) if username is not None else None

        return super(_DjangoUserInfoRetriever, self).get_username()

    def get_name(self) -> Optional[str]:
        if not asm_config._user_model_name_field:
            get_full_name = getattr(self.user, "get_full_name", None)
            if callable(get_full_name):
                try:
                    full_name = get_full_name()
                    if full_name is not None:
                        return str(full_name)
                except Exception:
                    log.debug("User model get_full_name member produced an exception: ", exc_info=True)

            first_name = getattr(self.user, "first_name", None)
            last_name = getattr(self.user, "last_name", None)
            if first_name is not None and last_name is not None:
                return "%s %s" % (first_name, last_name)

        return super(_DjangoUserInfoRetriever, self).get_name()

    def get_user_email(self) -> Optional[str]:
        email_field = getattr(type(self.user), "EMAIL_FIELD", None)
        if email_field is not None and not asm_config._user_model_name_field:
            email = getattr(self.user, email_field, None)
            return str(email) if email is not None else None

        return super(_DjangoUserInfoRetriever, self).get_user_email()
