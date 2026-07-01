import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Level(models.Model):
    level_number = models.PositiveSmallIntegerField(primary_key=True)
    xp_required = models.PositiveIntegerField()  # XP acumulado para atingir este nível
    title = models.CharField(max_length=80)      # "Leitor Iniciante", ...

    class Meta:
        ordering = ["level_number"]
    # linhas geradas por fórmula no seed; editáveis via Admin


class XPTransaction(models.Model):
    class Reason(models.TextChoices):
        PAGES_READ = "pages_read", "Páginas lidas"
        BOOK_COMPLETED = "book_completed", "Livro concluído"
        REVIEW_WRITTEN = "review_written", "Resenha escrita"
        GOAL_MET = "goal_met", "Meta cumprida"
        QUEST_COMPLETED = "quest_completed", "Quest concluída"
        ACHIEVEMENT_UNLOCKED = "achievement_unlocked", "Conquista desbloqueada"
        CORRECTION = "correction", "Correção"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT (não CASCADE): impede user.delete() quando há histórico de XP,
    # com ProtectedError limpo no ORM — coerente com o ledger imutável.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="xp_transactions")
    amount = models.IntegerField()  # pode ser negativo (correção)
    reason = models.CharField(max_length=24, choices=Reason.choices)
    source_type = models.CharField(max_length=40, blank=True)  # sem FK rígida (ledger)
    source_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"], name="ix_xp_user_created")]

    # Imutabilidade: bloqueia UPDATE e DELETE no nível do model.
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("XPTransaction é imutável — crie uma transação de correção.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("XPTransaction não pode ser apagada.")


class Streak(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="streak")
    current_count = models.PositiveIntegerField(default=0)
    longest_count = models.PositiveIntegerField(default=0)
    last_active_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(longest_count__gte=F("current_count")), name="ck_streak_longest"),
        ]


class StreakFreeze(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="streak_freezes")
    used_on = models.DateField()
    period_key = models.CharField(max_length=7)  # "2026-06"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "period_key"], name="uq_freeze_user_period"),
        ]
