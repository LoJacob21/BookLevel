"""Endpoint da timeline pessoal (montado sob /me). Somente leitura.

Eventos são gerados exclusivamente pelos services de domínio; a API só
lista os do próprio user, mais recentes primeiro.
"""

import uuid
from datetime import date, datetime

from ninja import Router, Schema
from ninja.pagination import PageNumberPagination, paginate

from timeline.models import TimelineEvent

router = Router(tags=["timeline"])


class TimelineEventOut(Schema):
    id: uuid.UUID
    type: str
    event_date: date
    payload: dict
    visibility: str
    created_at: datetime


@router.get("/timeline", response=list[TimelineEventOut])
@paginate(PageNumberPagination, page_size=20)
def my_timeline(request):
    # event_date é a data semântica (fuso do user); created_at desempata
    # eventos do mesmo dia.
    return TimelineEvent.objects.filter(user=request.auth).order_by(
        "-event_date", "-created_at"
    )
