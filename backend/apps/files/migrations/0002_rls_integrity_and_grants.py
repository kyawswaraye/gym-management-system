"""Forced tenant RLS for file metadata.

Runtime may select, insert, and update. Physical deletion follows retention
policy, so this migration does not grant DELETE. The delete policy still
scopes any later maintenance role that receives that privilege explicitly.
"""

from django.db import migrations

FORWARD_SQL = """
ALTER TABLE files_file_asset ENABLE ROW LEVEL SECURITY;
ALTER TABLE files_file_asset FORCE ROW LEVEL SECURITY;
CREATE POLICY files_file_asset_tenant_select ON files_file_asset
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY files_file_asset_tenant_insert ON files_file_asset
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY files_file_asset_tenant_update ON files_file_asset
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY files_file_asset_tenant_delete ON files_file_asset
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE files_file_asset
  ADD CONSTRAINT file_asset_uploader_same_gym_fk
  FOREIGN KEY (gym_id, uploaded_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE files_file_asset
  ADD CONSTRAINT fileasset_object_key_gym_prefix_ck
  CHECK (split_part(object_key, '/', 1) = gym_id::text);

GRANT SELECT, INSERT, UPDATE ON files_file_asset TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON files_file_asset FROM gym_runtime;
ALTER TABLE files_file_asset
  DROP CONSTRAINT IF EXISTS fileasset_object_key_gym_prefix_ck;
ALTER TABLE files_file_asset
  DROP CONSTRAINT IF EXISTS file_asset_uploader_same_gym_fk;
DROP POLICY IF EXISTS files_file_asset_tenant_delete ON files_file_asset;
DROP POLICY IF EXISTS files_file_asset_tenant_update ON files_file_asset;
DROP POLICY IF EXISTS files_file_asset_tenant_insert ON files_file_asset;
DROP POLICY IF EXISTS files_file_asset_tenant_select ON files_file_asset;
ALTER TABLE files_file_asset NO FORCE ROW LEVEL SECURITY;
ALTER TABLE files_file_asset DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0001_initial"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
