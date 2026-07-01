import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Goal(models.Model):
    class Metric(models.TextChoices):
        PAGES = "pages", "Páginas"
        BOOKS = "books", "Livros"
        MINUTES = "minutes", "Minutos"

    class Period(models.TextChoices):
        DAILY = "daily", "Diária"
        WEEKLY = "weekly", "Semanal"
        MONTHLY = "monthly", "Mensal"
        YEARLY = "yearly", "Anual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="goals")
    user_book = models.ForeignKey("library.UserBook", null=True, blank=True, on_delete=models.CASCADE, related_name="goals")
    metric = models.CharField(max_length=10, choices=Metric.choices)
    period = models.CharField(max_length=10, choices=Period.choices)
    target_value = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(target_value__gt=0), name="ck_goal_target_pos"),
            # 1 meta global ativa por user+metric+period
            models.UniqueConstraint(
                fields=["user", "metric", "period"],
                condition=Q(user_book__isnull=True, is_active=True),
                name="uq_goal_global_active",
            ),
            # 1 meta por livro ativa por user_book+metric+period
            models.UniqueConstraint(
                fields=["user_book", "metric", "period"],
                condition=Q(user_book__isnull=False, is_active=True),
                name="uq_goal_book_active",
            ),
        ]
