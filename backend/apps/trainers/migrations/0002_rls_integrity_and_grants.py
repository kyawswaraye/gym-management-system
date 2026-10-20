from django.db import migrations

FORWARD_SQL = r"""
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE trainers_trainer_assignment
  ADD CONSTRAINT trainer_asg_trainer_same_gym_fk
  FOREIGN KEY (gym_id, trainer_staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_trainer_assignment
  ADD CONSTRAINT trainer_asg_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_trainer_assignment
  ADD CONSTRAINT trainer_asg_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE trainers_trainer_assignment
  ADD CONSTRAINT trainer_asg_active_overlap_excl
  EXCLUDE USING gist (
    gym_id WITH =,
    trainer_staff_profile_id WITH =,
    member_id WITH =,
    daterange(
      effective_from,
      COALESCE(effective_until, 'infinity'::date),
      '[]'
    ) WITH &&
  )
  WHERE (status = 'ACTIVE');

CREATE FUNCTION app_guard_trainer_assignment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.effective_until IS NOT NULL AND NEW.effective_until < NEW.effective_from THEN
    RAISE EXCEPTION 'assignment_guard: end date is before the start date';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'ACTIVE' OR NEW.ended_at IS NOT NULL THEN
      RAISE EXCEPTION 'assignment_guard: new assignments start active';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.status <> 'ACTIVE' THEN
    RAISE EXCEPTION 'assignment_guard: closed assignments are immutable';
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
     OR NEW.member_id IS DISTINCT FROM OLD.member_id
     OR NEW.effective_from IS DISTINCT FROM OLD.effective_from THEN
    RAISE EXCEPTION 'assignment_guard: identity columns are immutable';
  END IF;
  IF NEW.status = 'ENDED' THEN
    IF NEW.ended_at IS NULL
       OR btrim(NEW.reason) = ''
       OR NEW.effective_until IS NULL
       OR NEW.effective_until < NEW.effective_from THEN
      RAISE EXCEPTION 'assignment_guard: ended assignment evidence is incomplete';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status = 'CANCELLED' THEN
    IF NEW.ended_at IS NULL
       OR btrim(NEW.reason) = ''
       OR NEW.effective_until IS DISTINCT FROM OLD.effective_until THEN
      RAISE EXCEPTION 'assignment_guard: cancelled assignment evidence is incomplete';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'assignment_guard: assignment status transition is invalid';
END;
$guard$;

CREATE FUNCTION app_guard_trainer_assignment_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'assignment_guard: trainer assignments cannot be deleted';
END;
$guard$;

CREATE TRIGGER trainer_assignment_guard_trg
BEFORE INSERT OR UPDATE ON trainers_trainer_assignment
FOR EACH ROW EXECUTE FUNCTION app_guard_trainer_assignment();
CREATE TRIGGER trainer_assignment_delete_trg
BEFORE DELETE ON trainers_trainer_assignment
FOR EACH ROW EXECUTE FUNCTION app_guard_trainer_assignment_delete();

ALTER TABLE trainers_trainer_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_trainer_assignment FORCE ROW LEVEL SECURITY;
CREATE POLICY trainers_trainer_assignment_select ON trainers_trainer_assignment
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY trainers_trainer_assignment_insert ON trainers_trainer_assignment
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_trainer_assignment_update ON trainers_trainer_assignment
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_trainer_assignment_delete ON trainers_trainer_assignment
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_trainer_assignment FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE trainers_trainer_assignment TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_trainer_assignment() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_trainer_assignment_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_trainer_assignment() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_trainer_assignment_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_trainer_assignment_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_trainer_assignment() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE trainers_trainer_assignment FROM gym_runtime;
DROP POLICY IF EXISTS trainers_trainer_assignment_delete ON trainers_trainer_assignment;
DROP POLICY IF EXISTS trainers_trainer_assignment_update ON trainers_trainer_assignment;
DROP POLICY IF EXISTS trainers_trainer_assignment_insert ON trainers_trainer_assignment;
DROP POLICY IF EXISTS trainers_trainer_assignment_select ON trainers_trainer_assignment;
ALTER TABLE trainers_trainer_assignment NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_trainer_assignment DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS trainer_assignment_delete_trg ON trainers_trainer_assignment;
DROP TRIGGER IF EXISTS trainer_assignment_guard_trg ON trainers_trainer_assignment;
DROP FUNCTION IF EXISTS app_guard_trainer_assignment_delete();
DROP FUNCTION IF EXISTS app_guard_trainer_assignment();
ALTER TABLE trainers_trainer_assignment
  DROP CONSTRAINT IF EXISTS trainer_asg_active_overlap_excl;
ALTER TABLE trainers_trainer_assignment
  DROP CONSTRAINT IF EXISTS trainer_asg_created_by_same_gym_fk;
ALTER TABLE trainers_trainer_assignment
  DROP CONSTRAINT IF EXISTS trainer_asg_member_same_gym_fk;
ALTER TABLE trainers_trainer_assignment
  DROP CONSTRAINT IF EXISTS trainer_asg_trainer_same_gym_fk;
"""


def seed_pt_feature(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    Plan = apps.get_model("tenants", "SubscriptionPlan")
    PlanFeature = apps.get_model("tenants", "SubscriptionPlanFeature")
    Gym = apps.get_model("tenants", "Gym")
    Entitlement = apps.get_model("tenants", "GymFeatureEntitlement")
    feature, _created = Feature.objects.update_or_create(
        code="pt",
        defaults={
            "name": "Personal training",
            "description": "Personal training module",
        },
    )
    plan = Plan.objects.filter(code="TRIAL").first()
    if plan is not None:
        PlanFeature.objects.update_or_create(
            subscription_plan=plan,
            feature=feature,
            defaults={"enabled": True, "limit_value": None},
        )
    for gym_id in Gym.objects.values_list("id", flat=True).iterator(chunk_size=100):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_id)])
        Entitlement.objects.get_or_create(
            gym_id=gym_id,
            feature=feature,
            defaults={
                "enabled": True,
                "source": "OVERRIDE",
                "created_by_membership_id": None,
            },
        )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0001_trainer_assignment"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
        migrations.RunPython(seed_pt_feature, migrations.RunPython.noop),
    ]
