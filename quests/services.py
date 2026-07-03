"""Serviços de quests: progressão, conclusão e desbloqueio de conquistas.

Os handlers públicos (handle_pages_read, handle_book_finished, handle_streak)
NÃO abrem transação própria: rodam DENTRO da transação do caller
(register_reading_session / finish_reading), para que progresso de quest,
XP e eventos de timeline sejam atômicos com o gatilho que os disparou.

Direção de dependência: library -> quests -> gamification/timeline.
Este módulo importa library.models apenas para métricas agregadas lifetime
(R7) — nunca library.services.
"""

from django.db import transaction
from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from gamification.models import Streak, XPTransaction
from gamification.services import create_level_up_event, grant_xp, today_for
from library.models import ReadingSession, UserBook
from timeline.models import TimelineEvent

from .models import Achievement, Quest, UserAchievement, UserQuest

# criteria_type válidos no MVP (R2).
PAGES_READ = "pages_read"
BOOKS_FINISHED = "books_finished"
STREAK_DAYS = "streak_days"


def handle_pages_read(user, pages_delta: int) -> None:
    """Progressão pages_read: soma o delta da sessão (R4).

    Exige transação ativa do caller (chamado após o grant_xp da sessão).
    """
    _require_atomic()
    _auto_enroll(user, PAGES_READ)
    UserQuest.objects.filter(
        user=user,
        status=UserQuest.Status.IN_PROGRESS,
        quest__criteria_type=PAGES_READ,
    ).update(progress_value=F("progress_value") + pages_delta)
    _complete_reached_quests(user, PAGES_READ)
    _evaluate_standalone_achievements(user, PAGES_READ)


def handle_book_finished(user) -> None:
    """Progressão books_finished: +1 por livro concluído (R4).

    Exige transação ativa do caller (chamado por finish_reading).
    """
    _require_atomic()
    _auto_enroll(user, BOOKS_FINISHED)
    UserQuest.objects.filter(
        user=user,
        status=UserQuest.Status.IN_PROGRESS,
        quest__criteria_type=BOOKS_FINISHED,
    ).update(progress_value=F("progress_value") + 1)
    _complete_reached_quests(user, BOOKS_FINISHED)
    _evaluate_standalone_achievements(user, BOOKS_FINISHED)


def handle_streak(user, current_count: int) -> None:
    """Progressão streak_days: guarda o maior current_count visto (R4).

    Exige transação ativa do caller (chamado quando streak_kept=True).
    """
    _require_atomic()
    _auto_enroll(user, STREAK_DAYS)
    UserQuest.objects.filter(
        user=user,
        status=UserQuest.Status.IN_PROGRESS,
        quest__criteria_type=STREAK_DAYS,
    ).update(progress_value=Greatest("progress_value", Value(current_count)))
    _complete_reached_quests(user, STREAK_DAYS)
    _evaluate_standalone_achievements(user, STREAK_DAYS)


def _require_atomic() -> None:
    """Guarda defensiva: os handlers só podem rodar dentro de transaction.atomic."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "Handlers de quests exigem transação ativa (transaction.atomic) do caller."
        )


def _active_quests(criteria_type: str):
    """Quests elegíveis para auto-inscrição (R3): dentro da janela de validade
    (quando definida) e SEM escopo (event_id e community_id NULL)."""
    now = timezone.now()
    return (
        Quest.objects.filter(
            criteria_type=criteria_type,
            event_id__isnull=True,
            community_id__isnull=True,
        )
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=now))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=now))
    )


def _auto_enroll(user, criteria_type: str) -> None:
    """Auto-inscrição implícita (R3), lazy para repetíveis (R8).

    - Repetível: get_or_create de uma linha in_progress — se a anterior está
      completed, nasce outra (o índice parcial uq_userquest_active permite).
    - Não repetível: só inscreve se o user NUNCA jogou essa quest — concluída
      não volta a in_progress (nota do modelo relacional §6).
    get_or_create roda a criação em savepoint, então colisão concorrente com
    o índice parcial vira retry de leitura, sem poisonar a transação externa.
    """
    for quest in _active_quests(criteria_type):
        if quest.is_repeatable:
            UserQuest.objects.get_or_create(
                user=user, quest=quest, status=UserQuest.Status.IN_PROGRESS,
            )
        elif not UserQuest.objects.filter(user=user, quest=quest).exists():
            UserQuest.objects.get_or_create(
                user=user, quest=quest, status=UserQuest.Status.IN_PROGRESS,
            )


def _complete_reached_quests(user, criteria_type: str) -> None:
    """Completa (R5 + R6) toda UserQuest in_progress que atingiu o alvo.

    progress_value pode ultrapassar criteria_value (R4 — não trunca);
    a comparação é >= via F() cross-join.
    """
    reached = (
        UserQuest.objects.select_related("quest")
        .filter(
            user=user,
            status=UserQuest.Status.IN_PROGRESS,
            quest__criteria_type=criteria_type,
            progress_value__gte=F("quest__criteria_value"),
        )
    )
    for user_quest in reached:
        _complete_user_quest(user_quest)


def _complete_user_quest(user_quest: UserQuest) -> None:
    """R5: status/completed_at/XP/TimelineEvent. R6: achievements vinculados."""
    user = user_quest.user
    quest = user_quest.quest

    user_quest.status = UserQuest.Status.COMPLETED
    user_quest.completed_at = timezone.now()
    user_quest.save(update_fields=["status", "completed_at"])

    today = today_for(user)

    # XP da quest (R1/R5) — mesma guarda de amount > 0 dos services de leitura:
    # xp_reward == 0 não gera XPTransaction, mas o TimelineEvent sai mesmo assim.
    if quest.xp_reward > 0:
        grant_result = grant_xp(
            user,
            quest.xp_reward,
            XPTransaction.Reason.QUEST_COMPLETED,
            source_type="user_quest",
            source_id=user_quest.id,
        )
        if grant_result.level_changed:
            create_level_up_event(user, grant_result, today)

    TimelineEvent.objects.create(
        user=user,
        type=TimelineEvent.Type.QUEST_COMPLETED,
        event_date=today,
        visibility=TimelineEvent.Visibility.PRIVATE,
        payload={
            "quest_id": str(quest.id),
            "code": quest.code,
            "name": quest.name,
            "xp_reward": quest.xp_reward,
        },
    )

    # R6: achievements vinculados à quest completada.
    for achievement in quest.achievements.all():
        _unlock_achievement(user, achievement)


def _unlock_achievement(user, achievement: Achievement) -> None:
    """Desbloqueia (uma única vez) um achievement para o user (R6/R7).

    uq_userachievement é a rede de segurança contra duplo desbloqueio;
    get_or_create a usa como fonte de verdade — se já existe, no-op.
    """
    user_achievement, created = UserAchievement.objects.get_or_create(
        user=user, achievement=achievement,
    )
    if not created:
        return

    today = today_for(user)

    # XP do achievement — crédito INDEPENDENTE do XP da quest (R6),
    # com a mesma guarda de amount > 0.
    if achievement.xp_reward > 0:
        grant_result = grant_xp(
            user,
            achievement.xp_reward,
            XPTransaction.Reason.ACHIEVEMENT_UNLOCKED,
            source_type="user_achievement",
            source_id=user_achievement.id,
        )
        if grant_result.level_changed:
            create_level_up_event(user, grant_result, today)

    TimelineEvent.objects.create(
        user=user,
        type=TimelineEvent.Type.ACHIEVEMENT_UNLOCKED,
        event_date=today,
        visibility=TimelineEvent.Visibility.PRIVATE,
        payload={
            "achievement_id": str(achievement.id),
            "code": achievement.code,
            "name": achievement.name,
            "xp_reward": achievement.xp_reward,
        },
    )


def _evaluate_standalone_achievements(user, criteria_type: str) -> None:
    """R7: achievements independentes (source_quest NULL), contra métrica lifetime.

    Só avalia achievements SEM escopo (event_id NULL — mesmo princípio do R3)
    e com criteria_value definido (NULL = só desbloqueável via quest/manual).
    """
    candidates = Achievement.objects.filter(
        source_quest__isnull=True,
        criteria_type=criteria_type,
        criteria_value__isnull=False,
        event_id__isnull=True,
    ).exclude(user_achievements__user=user)

    if not candidates.exists():
        return

    metric = _lifetime_metric(user, criteria_type)
    for achievement in candidates:
        if metric >= achievement.criteria_value:
            _unlock_achievement(user, achievement)


def _lifetime_metric(user, criteria_type: str) -> int:
    """Métrica lifetime do user para avaliação de achievements (R7)."""
    if criteria_type == PAGES_READ:
        total = ReadingSession.objects.filter(user=user).aggregate(
            total=Sum(F("end_page") - F("start_page")),
        )["total"]
        return total or 0
    if criteria_type == BOOKS_FINISHED:
        return UserBook.objects.filter(user=user, status=UserBook.Status.LIDO).count()
    if criteria_type == STREAK_DAYS:
        streak = Streak.objects.filter(user=user).first()
        return streak.longest_count if streak else 0
    raise ValueError(f"criteria_type desconhecido: {criteria_type!r}")
