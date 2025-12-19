import uuid

from django.contrib.auth.models import AbstractBaseUser
from django.db import models


class UUIDUser(AbstractBaseUser):
    """
    Minimal user model with UUID primary key for testing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    USERNAME_FIELD = "username"

    @property
    def is_authenticated(self):
        return True
