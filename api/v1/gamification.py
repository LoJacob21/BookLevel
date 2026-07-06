"""Endpoints de gamificação (montados sob /me). Somente leitura.

Perfil de progressão do próprio user (request.auth) e extrato do ledger.
Nenhuma escrita: XP só muda pelos services de domínio.
"""

import uuid
from datetime import datetime

from ninja import Router, Schema
from ninja.pagination import PageNumberPagination, paginate

from gamification.models import Level, Streak, XPTransaction

router = Router(tags=["gamification"])


class MeOut(Schema):
    nickname: str
    total_xp: int
    current_level: int
    level_title: str | None
    streak_current: int
    streak_longest: int


class XPTransactionOut(Schema):
    id: uuid.UUID
    amount: int
    reason: str
    source_type: str
    source_id: uuid.UUID | None
    created_at: datetime


@router.get("", response=MeOut)
def me(request):
    user = request.auth
    level_title = (
        Level.objects.filter(level_number=user.current_level)
        .values_list("title", flat=True)
        .first()
    )
    # Streak é criada no primeiro update_streak — user novo ainda não tem.
    streak = Streak.objects.filter(user=user).first()
    return {
        "nickname": user.nickname,
        "total_xp": user.total_xp,
        "current_level": user.current_level,
        "level_title": level_title,
        "streak_current": streak.current_count if streak else 0,
        "streak_longest": streak.longest_count if streak else 0,
    }


@router.get("/xp-ledger", response=list[XPTransactionOut])
@paginate(PageNumberPagination, page_size=20)
def xp_ledger(request):
    return XPTransaction.objects.filter(user=request.auth).order_by("-created_at")
