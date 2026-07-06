"""Endpoints de quests e conquistas do próprio user (montados sob /me).

Somente leitura: inscrição, progresso e desbloqueio acontecem nos
services de quests, disparados pelos eventos de leitura.
"""

import uuid
from datetime import datetime

from ninja import Router, Schema
from ninja.errors import HttpError

from quests.models import UserAchievement, UserQuest

router = Router(tags=["quests"])


class QuestOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    description: str
    criteria_type: str
    criteria_value: int
    xp_reward: int
    is_repeatable: bool


class UserQuestOut(Schema):
    id: uuid.UUID
    quest: QuestOut
    status: str
    progress_value: int
    completed_at: datetime | None


class AchievementOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    description: str
    criteria_type: str
    criteria_value: int | None
    xp_reward: int


class UserAchievementOut(Schema):
    id: uuid.UUID
    achievement: AchievementOut
    unlocked_at: datetime


@router.get("/quests", response=list[UserQuestOut])
def my_quests(request, status: str | None = None):
    user_quests = (
        UserQuest.objects.filter(user=request.auth)
        .select_related("quest")
        .order_by("quest__code", "-completed_at")
    )
    if status is not None:
        if status not in UserQuest.Status.values:
            raise HttpError(
                400, f"status inválido: {status!r}. Use um de {UserQuest.Status.values}."
            )
        user_quests = user_quests.filter(status=status)
    return user_quests


@router.get("/achievements", response=list[UserAchievementOut])
def my_achievements(request):
    return (
        UserAchievement.objects.filter(user=request.auth)
        .select_related("achievement")
        .order_by("-unlocked_at")
    )
