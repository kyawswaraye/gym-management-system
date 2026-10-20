from django.db import migrations

FORWARD_SQL = """
ALTER TABLE announcements_announcement
  ADD CONSTRAINT announcement_body_ck
  CHECK (char_length(body) BETWEEN 1 AND 4000);
"""

REVERSE_SQL = """
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcement_body_ck;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("announcements", "0002_announcement_rls_and_guards"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="announcement", name="announcement_body_ck"),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
