from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    id = models.TextField(
        verbose_name="user_id",
        max_length=255,
        unique=True,
        primary_key=True,
    )
