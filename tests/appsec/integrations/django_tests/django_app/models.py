"""Django models for CVE-2024-53908 testing.

This module contains the Item model with a JSONField that is vulnerable
to SQL injection when using Oracle database with HasKey lookup.
"""

from django.db import models


class Item(models.Model):
    """Model with JSONField for testing CVE-2024-53908.

    This model demonstrates the vulnerability where direct use of
    django.db.models.fields.json.HasKey lookup with Oracle database
    is subject to SQL injection if untrusted data is used as the lhs value.
    """

    name = models.CharField(max_length=100)
    data = models.JSONField()

    def __str__(self):
        """String representation of the Item."""
        return self.name

    class Meta:
        """Meta options for Item model."""

        db_table = "cve_test_item"
