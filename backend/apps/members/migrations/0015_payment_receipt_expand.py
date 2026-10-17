# Expand payment and receipt columns before legacy reconciliation.
# New receipt identity columns stay nullable until 0016 backfills them.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cashbook", "0001_initial"),
        ("files", "0003_profile_photo_contract"),
        ("members", "0014_device_ingestion_actor"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("MEMBERSHIP", "Membership"),
                    ("PT", "Pt"),
                    ("PRODUCT", "Product"),
                    ("CLASS", "Class"),
                    ("OTHER", "Other"),
                ],
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="member",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="payments",
                to="members.member",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="reversal_payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="reversed_by",
                to="members.payment",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="cancelled_by_membership_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="posted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="posted_by_membership_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="voided_by_membership_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="payment",
            name="cash_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="payments",
                to="cashbook.cashsession",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="cash_transaction",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="source_payment",
                to="cashbook.cashtransaction",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="legacy_unlinked_cash",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="payment",
            name="created_with_reception_scope",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="payment",
            name="submitted_with_reception_scope",
            field=models.BooleanField(default=False),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["gym", "membership", "status"], name="payment_gym_membership_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["gym", "purpose", "business_date"], name="payment_gym_purpose_date_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["gym", "method", "business_date"], name="payment_gym_method_date_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["gym", "original_payment"], name="payment_gym_original_idx"),
        ),
        migrations.AlterField(
            model_name="receipt",
            name="payment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="receipts",
                to="members.payment",
            ),
        ),
        migrations.AddField(
            model_name="receipt",
            name="sequence_key",
            field=models.CharField(blank=True, max_length=48, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="sequence_value",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="sequence_year",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="gym_code",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="gym_name",
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="gym_address",
            field=models.CharField(blank=True, default="", max_length=800),
        ),
        migrations.AddField(
            model_name="receipt",
            name="status",
            field=models.CharField(
                choices=[("ISSUED", "Issued"), ("VOID", "Void"), ("REPLACED", "Replaced")],
                default="ISSUED",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="receipt",
            name="voided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="voided_by_membership_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="void_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="replacement_receipt",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="replaced_receipts",
                to="members.receipt",
            ),
        ),
        migrations.AddField(
            model_name="receipt",
            name="rendered_file_asset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="receipts",
                to="files.fileasset",
            ),
        ),
    ]
