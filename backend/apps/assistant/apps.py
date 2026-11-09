from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assistant"
    label = "assistant"

    def ready(self) -> None:
        # Validation must not raise. A missing Gemini key leaves the assistant off.
        from apps.assistant.configuration import assistant_configuration

        assistant_configuration()
