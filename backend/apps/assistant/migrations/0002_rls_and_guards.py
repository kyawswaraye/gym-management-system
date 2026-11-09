"""Same-gym keys, forced RLS, deferred effect check, and runtime grants."""

from django.db import migrations

_TABLES = (
    "assistant_conversation",
    "assistant_goal",
    "assistant_message",
    "assistant_workflow",
    "assistant_workflow_step",
    "assistant_tool_execution",
    "assistant_attachment_reference",
)


def _policies(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY {table}_tenant_select ON {table}
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_insert ON {table}
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_update ON {table}
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_delete ON {table}
  FOR DELETE USING (gym_id = app_current_gym_id());
REVOKE ALL ON TABLE {table} FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO gym_runtime;
CREATE TRIGGER {table}_identity_trg
BEFORE UPDATE ON {table}
FOR EACH ROW EXECUTE FUNCTION app_guard_assistant_identity();
CREATE TRIGGER {table}_delete_trg
BEFORE DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION app_guard_assistant_delete();
"""


def _drop_policies(table: str) -> str:
    return f"""
DROP TRIGGER IF EXISTS {table}_delete_trg ON {table};
DROP TRIGGER IF EXISTS {table}_identity_trg ON {table};
REVOKE ALL ON TABLE {table} FROM gym_runtime;
DROP POLICY IF EXISTS {table}_tenant_delete ON {table};
DROP POLICY IF EXISTS {table}_tenant_update ON {table};
DROP POLICY IF EXISTS {table}_tenant_insert ON {table};
DROP POLICY IF EXISTS {table}_tenant_select ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


FORWARD_SQL = r"""
ALTER TABLE assistant_conversation
  DROP CONSTRAINT IF EXISTS assistant_conversation_created_by_membership_id_fkey;
ALTER TABLE assistant_conversation
  ADD CONSTRAINT assistant_conv_actor_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE assistant_goal
  DROP CONSTRAINT IF EXISTS assistant_goal_created_by_membership_id_fkey;
ALTER TABLE assistant_goal
  DROP CONSTRAINT IF EXISTS assistant_goal_conversation_id_fkey;
ALTER TABLE assistant_goal
  ADD CONSTRAINT assistant_goal_actor_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE assistant_goal
  ADD CONSTRAINT assistant_goal_conversation_same_gym_fk
  FOREIGN KEY (gym_id, conversation_id)
  REFERENCES assistant_conversation (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE assistant_message
  DROP CONSTRAINT IF EXISTS assistant_message_author_membership_id_fkey;
ALTER TABLE assistant_message
  DROP CONSTRAINT IF EXISTS assistant_message_conversation_id_fkey;
ALTER TABLE assistant_message
  DROP CONSTRAINT IF EXISTS assistant_message_goal_id_fkey;
ALTER TABLE assistant_message
  ADD CONSTRAINT assistant_message_actor_same_gym_fk
  FOREIGN KEY (gym_id, author_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE assistant_message
  ADD CONSTRAINT assistant_message_conversation_same_gym_fk
  FOREIGN KEY (gym_id, conversation_id)
  REFERENCES assistant_conversation (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE assistant_message
  ADD CONSTRAINT assistant_message_goal_same_gym_fk
  FOREIGN KEY (gym_id, goal_id)
  REFERENCES assistant_goal (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE assistant_workflow
  DROP CONSTRAINT IF EXISTS assistant_workflow_goal_id_fkey;
ALTER TABLE assistant_workflow
  ADD CONSTRAINT assistant_workflow_goal_same_gym_fk
  FOREIGN KEY (gym_id, goal_id)
  REFERENCES assistant_goal (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE assistant_workflow_step
  DROP CONSTRAINT IF EXISTS assistant_workflowstep_goal_id_fkey;
ALTER TABLE assistant_workflow_step
  DROP CONSTRAINT IF EXISTS assistant_workflowstep_workflow_id_fkey;
ALTER TABLE assistant_workflow_step
  ADD CONSTRAINT assistant_step_goal_same_gym_fk
  FOREIGN KEY (gym_id, goal_id)
  REFERENCES assistant_goal (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE assistant_workflow_step
  ADD CONSTRAINT assistant_step_workflow_same_gym_fk
  FOREIGN KEY (gym_id, workflow_id)
  REFERENCES assistant_workflow (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE assistant_tool_execution
  DROP CONSTRAINT IF EXISTS assistant_toolexecution_goal_id_fkey;
ALTER TABLE assistant_tool_execution
  DROP CONSTRAINT IF EXISTS assistant_toolexecution_step_id_fkey;
ALTER TABLE assistant_tool_execution
  ADD CONSTRAINT assistant_execution_goal_same_gym_fk
  FOREIGN KEY (gym_id, goal_id)
  REFERENCES assistant_goal (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE assistant_tool_execution
  ADD CONSTRAINT assistant_execution_step_same_gym_fk
  FOREIGN KEY (gym_id, step_id)
  REFERENCES assistant_workflow_step (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE assistant_attachment_reference
  DROP CONSTRAINT IF EXISTS assistant_attachmentreference_goal_id_fkey;
ALTER TABLE assistant_attachment_reference
  DROP CONSTRAINT IF EXISTS assistant_attachmentreference_file_asset_id_fkey;
ALTER TABLE assistant_attachment_reference
  ADD CONSTRAINT assistant_attachment_goal_same_gym_fk
  FOREIGN KEY (gym_id, goal_id)
  REFERENCES assistant_goal (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE assistant_attachment_reference
  ADD CONSTRAINT assistant_attachment_file_same_gym_fk
  FOREIGN KEY (gym_id, file_asset_id)
  REFERENCES files_file_asset (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_assistant_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'assistant_guard: identity columns are immutable';
  END IF;
  IF TG_TABLE_NAME = 'assistant_goal' THEN
    IF NEW.version < OLD.version THEN
      RAISE EXCEPTION 'assistant_guard: version cannot decrease';
    END IF;
  END IF;
  IF TG_TABLE_NAME = 'assistant_tool_execution' THEN
    IF NEW.execution_identity IS DISTINCT FROM OLD.execution_identity
       OR NEW.goal_id IS DISTINCT FROM OLD.goal_id
       OR NEW.step_id IS DISTINCT FROM OLD.step_id
       OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
       OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash THEN
      RAISE EXCEPTION 'assistant_guard: execution identity is immutable';
    END IF;
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_assistant_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'assistant_guard: assistant records cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_guard_assistant_effect()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  reference text;
BEGIN
  SELECT effect_reference INTO reference
  FROM public.assistant_tool_execution
  WHERE id = NEW.id;
  IF reference IS NULL OR reference = '' THEN
    RAISE EXCEPTION 'assistant_guard: effect reference is required at commit'
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER assistant_tool_execution_effect_trg
AFTER INSERT OR UPDATE OF effect_reference ON assistant_tool_execution
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_guard_assistant_effect();
""" + "".join(_policies(table) for table in _TABLES) + r"""
REVOKE ALL ON FUNCTION app_guard_assistant_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_assistant_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_assistant_effect() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_assistant_identity() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_assistant_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_assistant_effect() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = (
    "".join(_drop_policies(table) for table in reversed(_TABLES))
    + r"""
DROP TRIGGER IF EXISTS assistant_tool_execution_effect_trg ON assistant_tool_execution;
REVOKE ALL ON FUNCTION app_guard_assistant_effect() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_assistant_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_assistant_identity() FROM gym_runtime, gym_migration;
DROP FUNCTION IF EXISTS app_guard_assistant_effect();
DROP FUNCTION IF EXISTS app_guard_assistant_delete();
DROP FUNCTION IF EXISTS app_guard_assistant_identity();
"""
)


class Migration(migrations.Migration):
    dependencies = [
        ("assistant", "0001_initial_records"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
