"""Serviços de biblioteca.

register_reading_session() é a peça que conecta o registro de leitura à
gamificação (XP + ofensiva) e à timeline. library depende de gamification e
timeline (o inverso nunca acontece), então não há import circular.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Greatest

from gamification.models import Level, XPTransaction
from gamification.services import grant_xp, update_streak
from timeline.models import TimelineEvent

from .models import ReadingSession, UserBook


@transaction.atomic
def register_reading_session(
    user_book: UserBook,
    start_page: int,
    end_page: int,
    duration_minutes: int | None,
    occurred_on: date,
) -> ReadingSession:
    """Registra uma sessão de leitura e propaga seus efeitos.

    Efeitos, na mesma transação: XP por página lida (1 XP/página),
    avanço de current_page (nunca regride), atualização da ofensiva e
    eventos de timeline (LEVEL_UP e STREAK_KEPT). NÃO decide o ciclo de
    vida do UserBook (status/started_at/finished_at) nem concede o bônus
    de conclusão de livro — isso é do futuro service de library.
    """
    user = user_book.user

    # 1. Invariante de aplicação (cross-table, fora de CHECK por decisão do
    #    modelo): não se pode ler além do total de páginas do livro. Valida
    #    ANTES de qualquer escrita.
    if end_page > user_book.book.total_pages:
        raise ValidationError(
            f"end_page ({end_page}) não pode exceder o total de páginas do "
            f"livro ({user_book.book.total_pages})."
        )

    # 2. Cria a sessão (user denormalizado a partir do user_book).
    session = ReadingSession.objects.create(
        user_book=user_book,
        user=user,
        start_page=start_page,
        end_page=end_page,
        duration_minutes=duration_minutes,
        occurred_on=occurred_on,
    )

    # 3. XP: 1 por página lida. Só concede se houve avanço.
    pages_delta = end_page - start_page
    grant_result = None
    if pages_delta > 0:
        grant_result = grant_xp(
            user,
            pages_delta,
            XPTransaction.Reason.PAGES_READ,
            source_type="reading_session",
            source_id=session.id,
        )

    # 4. Avança current_page para o MAIOR entre o atual e end_page — nunca
    #    regride por causa de uma sessão lançada fora de ordem. Um único
    #    update() atômico com Greatest evita janela de corrida.
    UserBook.objects.filter(pk=user_book.pk).update(
        current_page=Greatest("current_page", Value(end_page)),
    )

    # 5. Ofensiva (streak) do dia da sessão.
    streak_result = update_streak(user, occurred_on)

    # 6. TimelineEvent de LEVEL_UP (único tipo de nível que este service cria).
    if grant_result is not None and grant_result.level_changed:
        new_title = (
            Level.objects.filter(level_number=grant_result.new_level)
            .values_list("title", flat=True)
            .first()
        )
        TimelineEvent.objects.create(
            user=user,
            type=TimelineEvent.Type.LEVEL_UP,
            event_date=occurred_on,
            visibility=TimelineEvent.Visibility.PRIVATE,
            payload={
                "old_level": grant_result.old_level,
                "new_level": grant_result.new_level,
                "title": new_title,
            },
        )

    # 7. TimelineEvent de STREAK_KEPT quando a ofensiva foi mantida.
    if streak_result.streak_kept:
        TimelineEvent.objects.create(
            user=user,
            type=TimelineEvent.Type.STREAK_KEPT,
            event_date=occurred_on,
            visibility=TimelineEvent.Visibility.PRIVATE,
            payload={
                "current_count": streak_result.current_count,
                "longest_count": streak_result.longest_count,
            },
        )

    # 8. Retorna a sessão criada.
    return session
