"""Index member payment history in its published order.

``GET /api/v1/members/{id}/payments/`` filters one gym and member, then orders
``received_at DESC, id DESC`` and stops at ``LIMIT``. ``payment_gym_member_idx``
led with those columns but could not provide the order, so PostgreSQL could
sort an unbounded history first. Add the matching index before dropping the
redundant one.
"""

from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [("members", "0008_membership_sale_lifecycle")]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["gym", "member", "-received_at", "-id"],
                name="payment_gym_member_hist_idx",
            ),
        ),
        migrations.RemoveIndex(
            model_name="payment",
            name="payment_gym_member_idx",
        ),
    ]
