from django.db import migrations


FORWARD_SQL = """
ALTER TABLE members_payment ADD CONSTRAINT payment_original_same_gym_fk
  FOREIGN KEY (gym_id, original_payment_id)
  REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment ADD CONSTRAINT payment_not_own_original_ck
  CHECK (original_payment_id IS NULL OR original_payment_id <> id);
"""

REVERSE_SQL = """
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_not_own_original_ck;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_original_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0004_payment_entry_type_payment_original_payment"),
    ]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
