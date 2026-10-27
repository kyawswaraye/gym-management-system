from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0036_member_google_login_bootstrap"),
    ]

    operations = [
        migrations.AddField(
            model_name="gymsettings",
            name="printer_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="gymsettings",
            name="printer_footer",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="gymsettings",
            name="printer_print_address",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="gymsettings",
            name="printer_print_logo",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="gymsettings",
            name="printer_print_phone",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="gymsettings",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    printer_footer__regex=r"^([^\r\n]*\n){0,2}[^\r\n]*\n?$"
                ),
                name="settings_printer_footer_lines_ck",
            ),
        ),
    ]
