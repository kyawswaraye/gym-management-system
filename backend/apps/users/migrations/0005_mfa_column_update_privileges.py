from django.db import migrations

# Replay protection and enrollment write only these columns. Password,
# platform flags, and the rest of the identity row stay unwritable.
FORWARD_SQL = """
GRANT UPDATE (
  mfa_enabled,
  mfa_secret_encrypted,
  mfa_last_used_step,
  updated_at
) ON users_user TO gym_runtime;

DO $$
BEGIN
  IF has_table_privilege('gym_runtime', 'users_user', 'UPDATE') THEN
    RAISE EXCEPTION 'gym_runtime must not receive table-level UPDATE on users_user';
  END IF;
  IF NOT has_column_privilege(
    'gym_runtime', 'users_user', 'mfa_last_used_step', 'UPDATE'
  ) THEN
    RAISE EXCEPTION 'gym_runtime cannot update the MFA replay watermark';
  END IF;
  IF has_column_privilege('gym_runtime', 'users_user', 'password', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'is_platform_admin', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'is_superuser', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'is_active', 'UPDATE') THEN
    RAISE EXCEPTION 'gym_runtime can update a sensitive users_user column';
  END IF;
END $$;
"""

REVERSE_SQL = """
REVOKE UPDATE (
  mfa_enabled,
  mfa_secret_encrypted,
  mfa_last_used_step,
  updated_at
) ON users_user FROM gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_owner_mfa_foundation"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
