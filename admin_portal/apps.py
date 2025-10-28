from django.apps import AppConfig


class AdminPortalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'admin_portal'

    def ready(self):
        from . import signals
