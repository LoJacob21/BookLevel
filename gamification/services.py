"""Serviços de gamificação.

Regra inegociável 4: toda alteração de XP cria uma XPTransaction (ledger, fonte
da verdade) e atualiza o cache total_xp/current_level no User, na MESMA
transação de banco.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F

from .models import Level, Streak, StreakFreeze, XPTransaction

User = get_user_model()


@dataclass
class GrantResult:
    """Resultado de grant_xp(): a transação criada + efeito no nível."""

    user: object
    transaction: XPTransaction
    amount: int
    old_level: int
    new_level: int
    level_changed: bool


@dataclass
class StreakResult:
    """Resultado de update_streak(): efeito na ofensiva do usuário."""

    streak_kept: bool
    current_count: int
    longest_count: int
    broke: bool
    freeze_used: bool


@transaction.atomic
def grant_xp(user, amount, reason, source_type="", source_id=None) -> GrantResult:
    """Concede (ou corrige, com amount negativo) XP ao usuário.

    Cria a XPTransaction e atualiza o cache total_xp via update atômico
    (F("total_xp") + amount) para evitar race condition entre concessões
    concorrentes. Em seguida recalcula current_level a partir de Level.

    Retorna um GrantResult com a transação criada e a mudança de nível
    (old_level/new_level/level_changed), para o caller decidir efeitos
    colaterais (ex.: TimelineEvent de LEVEL_UP).
    """
    # Valida o reason antes de qualquer escrita — falha rápido, sem abrir
    # escrita parcial dentro da transação atômica.
    if reason not in XPTransaction.Reason.values:
        raise ValueError(
            f"reason inválido: {reason!r}. Use um de {XPTransaction.Reason.values}."
        )

    txn = XPTransaction.objects.create(
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

    old_level = user.current_level

    # current_level = maior nível cujo xp_required <= total_xp.
    computed_level = (
        Level.objects.filter(xp_required__lte=user.total_xp)
        .order_by("-level_number")
        .values_list("level_number", flat=True)
        .first()
    )

    level_changed = computed_level is not None and computed_level != old_level
    if level_changed:
        User.objects.filter(pk=user.pk).update(current_level=computed_level)
        user.current_level = computed_level

    return GrantResult(
        user=user,
        transaction=txn,
        amount=amount,
        old_level=old_level,
        new_level=user.current_level,
        level_changed=level_changed,
    )


@transaction.atomic
def update_streak(user, occurred_on: date) -> StreakResult:
    """Atualiza a ofensiva (streak) do usuário para o dia `occurred_on`.

    `occurred_on` já vem no fuso do usuário (é o dia da ReadingSession).
    Usa select_for_update() para serializar sessões concorrentes do mesmo
    usuário e evitar corrida no contador. Implementa as regras 2a-2d
    fechadas para o MVP (ver docstring de cada ramo).
    """
    # Garante que a linha exista, depois relê com lock para o resto da lógica.
    Streak.objects.get_or_create(user=user)
    streak = Streak.objects.select_for_update().get(user=user)

    last = streak.last_active_on
    kept = False
    broke = False
    freeze_used = False

    if last is None:
        # 2a. Primeira sessão do usuário: inicia a ofensiva. Início não é
        # continuidade, então NÃO dispara streak_kept.
        streak.current_count = 1
        streak.longest_count = max(streak.longest_count, 1)
        streak.last_active_on = occurred_on
    elif occurred_on == last:
        # 2b. Mesmo dia: idempotente — não altera nada, não dispara nada.
        pass
    elif occurred_on == last + timedelta(days=1):
        # 2c. Dia seguinte: continuidade da ofensiva.
        streak.current_count += 1
        streak.longest_count = max(streak.longest_count, streak.current_count)
        streak.last_active_on = occurred_on
        kept = True
    elif occurred_on > last + timedelta(days=1):
        # 2d. Gap (> 1 dia): tenta consumir um StreakFreeze do mês. A criação
        # bem-sucedida É o consumo — a UniqueConstraint(user, period_key)
        # garante 1 por mês. get_or_create usa savepoint, então um
        # IntegrityError concorrente não quebra a transação externa.
        period_key = occurred_on.strftime("%Y-%m")
        _, created = StreakFreeze.objects.get_or_create(
            user=user,
            period_key=period_key,
            defaults={"used_on": occurred_on},
        )
        if created:
            # Freeze consumido: mantém a ofensiva (não incrementa, não reseta).
            freeze_used = True
            streak.last_active_on = occurred_on
            kept = True
        else:
            # Já havia freeze usado neste mês: a ofensiva quebra.
            streak.current_count = 1
            streak.longest_count = max(streak.longest_count, 1)
            streak.last_active_on = occurred_on
            broke = True
    else:
        # occurred_on < last: sessão retroativa (fora de ordem cronológica).
        # Não regride a ofensiva — no-op, sem disparar streak_kept.
        pass

    streak.save()

    return StreakResult(
        streak_kept=kept,
        current_count=streak.current_count,
        longest_count=streak.longest_count,
        broke=broke,
        freeze_used=freeze_used,
    )
