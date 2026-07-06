import os

from celery import Celery

# Garante settings carregadas quando o worker sobe fora do manage.py.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'booklevel.settings')

app = Celery('booklevel')

# Toda config CELERY_* vem das settings do Django (namespace CELERY).
app.config_from_object('django.conf:settings', namespace='CELERY')

# Descobre tasks.py em cada app instalada.
app.autodiscover_tasks()
