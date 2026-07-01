import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class TimelineEvent(models.Model):
    class Type(models.TextChoices):
        BOOK_STARTED = "book_started", "Livro iniciado"
        BOOK_FINISHED = "book_finished", "Livro concluído"
        LEVEL_UP = "level_up", "Subiu de nível"
        ACHIEVEMENT_UNLOCKED = "achievement_unlocked", "Conquista desbloqueada"
        STREAK_KEPT = "streak_kept", "Ofensiva mantida"
        QUEST_COMPLETED = "quest_completed", "Quest concluída"
        DAILY_SUMMARY = "daily_summary", "Resumo do dia"

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Privado"
        FOLLOWERS = "followers", "Seguidores"
        PUBLIC = "public", "Público"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timeline_events")
    type = models.CharField(max_length=24, choices=Type.choices)
    payload = models.JSONField(default=dict)
    event_date = models.DateField()
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PRIVATE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # 1 resumo diário por usuário por dia
            models.UniqueConstraint(
                fields=["user", "event_date"],
                condition=Q(type="daily_summary"),
                name="uq_timeline_daily_summary",
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="ix_timeline_user_created")]
