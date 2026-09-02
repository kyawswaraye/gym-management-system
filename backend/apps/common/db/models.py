from __future__ import annotations

import uuid

from django.db import models


class UUIDTimestampModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantModel(UUIDTimestampModel):
    gym = models.ForeignKey("tenants.Gym", on_delete=models.RESTRICT)

    class Meta:
        abstract = True
