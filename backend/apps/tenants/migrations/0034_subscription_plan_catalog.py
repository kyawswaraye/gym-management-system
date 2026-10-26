"""Add monthly, quarterly, and yearly plans beside Trial.

The provision dropdown lists active plans. Trial was the only seeded plan,
so new gyms had one choice. These plans use the same currency and modules
as Trial. Price stays 0 until a platform admin sets it.
"""

from decimal import Decimal

from django.db import migrations

FEATURES = (
    ("members", "Members"),
    ("memberships", "Memberships"),
    ("finance", "Finance"),
    ("reports", "Reports"),
    ("pt", "Personal training"),
)

CATALOG = (
    ("MONTHLY", "Monthly", "MONTHLY"),
    ("QUARTERLY", "Quarterly", "QUARTERLY"),
    ("YEARLY", "Yearly", "YEARLY"),
)


def seed_catalog(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    Plan = apps.get_model("tenants", "SubscriptionPlan")
    PlanFeature = apps.get_model("tenants", "SubscriptionPlanFeature")

    features = []
    seen = set()
    for code, name in FEATURES:
        feature, _created = Feature.objects.get_or_create(
            code=code,
            defaults={"name": name, "description": f"{name} module"},
        )
        features.append(feature)
        seen.add(feature.id)

    trial = Plan.objects.filter(code="TRIAL").first()
    if trial is not None:
        links = PlanFeature.objects.filter(subscription_plan=trial).select_related(
            "feature"
        )
        for link in links:
            if link.feature_id in seen:
                continue
            features.append(link.feature)
            seen.add(link.feature_id)

    for code, name, interval in CATALOG:
        plan, _created = Plan.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "status": "ACTIVE",
                "price": Decimal("0.00"),
                "currency": "THB",
                "billing_interval": interval,
            },
        )
        for feature in features:
            PlanFeature.objects.get_or_create(
                subscription_plan=plan,
                feature=feature,
                defaults={"enabled": True, "limit_value": None},
            )


def unseed_catalog(apps, schema_editor):
    Plan = apps.get_model("tenants", "SubscriptionPlan")
    PlanFeature = apps.get_model("tenants", "SubscriptionPlanFeature")
    plans = Plan.objects.filter(code__in=[code for code, _name, _interval in CATALOG])
    PlanFeature.objects.filter(subscription_plan__in=plans).delete()
    plans.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0033_gym_logo"),
    ]

    operations = [
        migrations.RunPython(seed_catalog, unseed_catalog),
    ]
