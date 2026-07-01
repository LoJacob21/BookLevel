"""Serviços de gamificação.

Regra inegociável 4: toda alteração de XP cria uma XPTransaction (ledger, fonte
da verdade) e atualiza o cache total_xp/current_level no User, na MESMA
transação de banco.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F

from .models import Level, XPTransaction

User = get_user_model()


@transaction.atomic
def grant_xp(user, amount, reason, source_type="", source_id=None):
    """Concede (ou corrige, com amount negativo) XP ao usuário.

    Cria a XPTransaction e atualiza o cache total_xp via update atômico
    (F("total_xp") + amount) para evitar race condition entre concessões
    concorrentes. Em seguida recalcula current_level a partir de Level.
    """
    # Valida o reason antes de qualquer escrita — falha rápido, sem abrir
    # escrita parcial dentro da transação atômica.
    if reason not in XPTransaction.Reason.values:
        raise ValueError(
            f"reason inválido: {reason!r}. Use um de {XPTransaction.Reason.values}."
        )

    XPTransaction.objects.create(
        user=user,
        amount=amount,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
    )

    # Atualiza o cache total_xp na MESMA transação, de forma atômica.
    User.objects.filter(pk=user.pk).update(total_xp=F("total_xp") + amount)

    # Relê o total_xp já consolidado no banco.
    user.refresh_from_db(fields=["total_xp"])

    # current_level = maior nível cujo xp_required <= total_xp.
    new_level = (
        Level.objects.filter(xp_required__lte=user.total_xp)
        .order_by("-level_number")
        .values_list("level_number", flat=True)
        .first()
    )
    if new_level is not None and new_level != user.current_level:
        User.objects.filter(pk=user.pk).update(current_level=new_level)
        user.current_level = new_level

    return user
