import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Quest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    criteria_type = models.CharField(max_length=40)
    criteria_value = models.PositiveIntegerField()
    is_repeatable = models.BooleanField(default=False)
    # event_id/community_id: UUIDField solto (sem FK) — os apps events/communities
    # ainda não existem; viram ForeignKey numa migração simples quando existirem.
    event_id = models.UUIDField(null=True, blank=True)      # escopo opcional (events.SeasonEvent)
    community_id = models.UUIDField(null=True, blank=True)   # escopo opcional (communities.Community)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    xp_reward = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(criteria_value__gt=0), name="ck_quest_criteria_pos"),
            # CHECK (xp_reward >= 0) — paridade/legibilidade com o modelo
            # relacional §6, mesmo padrão da ck_achievement_xp_reward_nonneg.
            # Redundante com o CHECK automático do PositiveIntegerField no
            # PostgreSQL; mantida explícita para espelhar o schema documentado.
            models.CheckConstraint(condition=Q(xp_reward__gte=0), name="ck_quest_xp_reward_nonneg"),
        ]

    def __str__(self):
        return self.name


class UserQuest(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "Em andamento"
        COMPLETED = "completed", "Concluída"
        EXPIRED = "expired", "Expirada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_quests")
    quest = models.ForeignKey(Quest, on_delete=models.CASCADE, related_name="user_quests")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)
    progress_value = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Índice único parcial: no máximo 1 quest ativa por par user+quest,
            # mas permite várias linhas completed/expired no histórico (repetíveis).
            models.UniqueConstraint(
                fields=["user", "quest"],
                condition=Q(status="in_progress"),
                name="uq_userquest_active",
            ),
            # CHECK (progress_value >= 0) — paridade/legibilidade com o modelo
            # relacional §6. Redundante com o CHECK que o Django já gera
            # automaticamente para PositiveIntegerField no PostgreSQL; mantida
            # explícita para espelhar o schema documentado.
            models.CheckConstraint(condition=Q(progress_value__gte=0), name="ck_userquest_progress_nonneg"),
        ]
        indexes = [models.Index(fields=["user", "status"], name="ix_userquest_user_status")]


class Achievement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    criteria_type = models.CharField(max_length=40)
    # criteria_value nullable e SEM CHECK de positividade (difere de Quest,
    # onde criteria_value é NOT NULL e CHECK > 0) — conforme modelo relacional §6.
    criteria_value = models.PositiveIntegerField(null=True, blank=True)
    # SET_NULL: se a quest de origem for deletada, a conquista permanece.
    source_quest = models.ForeignKey(Quest, null=True, blank=True, on_delete=models.SET_NULL, related_name="achievements")
    event_id = models.UUIDField(null=True, blank=True)      # escopo opcional (events.SeasonEvent)
    xp_reward = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            # CHECK (xp_reward >= 0) — paridade/legibilidade com o modelo
            # relacional §6. No PostgreSQL o Django JÁ gera um CHECK automático
            # para PositiveIntegerField (no MySQL isso só existe a partir do
            # 8.0.16+/Django 3.0, via colunas unsigned). A constraint explícita
            # aqui é para espelhar o schema documentado, não por falta de proteção.
            models.CheckConstraint(condition=Q(xp_reward__gte=0), name="ck_achievement_xp_reward_nonneg"),
        ]

    def __str__(self):
        return self.name


class UserAchievement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_achievements")
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE, related_name="user_achievements")
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "achievement"], name="uq_userachievement"),
        ]
