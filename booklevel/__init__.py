# Carrega o app Celery junto com o Django, para @shared_task se ligar a ele.
from .celery import app as celery_app

__all__ = ('celery_app',)
