"""Serviços de biblioteca.

register_reading_session() é a peça que conecta o registro de leitura à
gamificação (XP + ofensiva) e à timeline. start_reading()/finish_reading()
cuidam do ciclo de vida do UserBook (status, started_at, finished_at) — que é
responsabilidade DESTE módulo, não do register_reading_session.

library depende de gamification, timeline e quests (o inverso nunca acontece:
quests só importa library.models, nunca library.services), então não há
import circular.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Greatest
from django.utils import timezone

from gamification.models import XPTransaction
from gamification.services import (
    create_level_up_event,
    grant_xp,
    today_for,
    update_streak,
)
from quests.services import handle_book_finished, handle_pages_read, handle_streak
from timeline.models import TimelineEvent

from .models import ReadingSession, UserBook

# Bônus fixo de conclusão de livro (R9).
BOOK_COMPLETED_XP = 100


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
    de conclusão de livro — isso é de start_reading()/finish_reading().
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

    # 3. XP: 1 por página lida. Só concede se houve avanço — e só então
    #    o evento conta para quests de pages_read.
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
        handle_pages_read(user, pages_delta)

    # 4. Avança current_page para o MAIOR entre o atual e end_page — nunca
    #    regride por causa de uma sessão lançada fora de ordem. Um único
    #    update() atômico com Greatest evita janela de corrida.
    UserBook.objects.filter(pk=user_book.pk).update(
        current_page=Greatest("current_page", Value(end_page)),
    )

    # 5. Ofensiva (streak) do dia da sessão. Continuidade mantida também
    #    conta para quests de streak_days.
    streak_result = update_streak(user, occurred_on)
    if streak_result.streak_kept:
        handle_streak(user, streak_result.current_count)

    # 6. TimelineEvent de LEVEL_UP, se o XP da sessão cruzou nível.
    if grant_result is not None and grant_result.level_changed:
        create_level_up_event(user, grant_result, occurred_on)

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


@transaction.atomic
def start_reading(user_book: UserBook) -> UserBook:
    """Transiciona o UserBook para "lendo" e registra o BOOK_STARTED.

    Transições válidas: quero_ler -> lendo e abandonado -> lendo (retomada).
    Releitura (lido -> lendo) está fora do MVP — bloqueada explicitamente.
    Qualquer outra origem levanta ValidationError.
    """
    # Relê a linha sob lock: a checagem de status no objeto em memória seria
    # TOCTOU — duas chamadas concorrentes passariam ambas pela validação.
    user_book = UserBook.objects.select_for_update().get(pk=user_book.pk)

    if user_book.status == UserBook.Status.LIDO:
        # Decisão de MVP, não caso esquecido: releitura exigiria zerar
        # progresso/sessões ou um segundo UserBook — fica para depois.
        raise ValidationError(
            "Releitura ainda não é suportada: este livro já foi concluído."
        )
    valid_from = (UserBook.Status.QUERO_LER, UserBook.Status.ABANDONADO)
    if user_book.status not in valid_from:
        raise ValidationError(
            f"Transição inválida: não é possível começar a ler um livro com "
            f"status {user_book.status!r} (esperado um de {[s.value for s in valid_from]})."
        )

    user = user_book.user
    user_book.status = UserBook.Status.LENDO
    # Não sobrescreve started_at numa retomada (abandonado -> lendo).
    if user_book.started_at is None:
        user_book.started_at = timezone.now()
    user_book.save(update_fields=["status", "started_at"])

    TimelineEvent.objects.create(
        user=user,
        type=TimelineEvent.Type.BOOK_STARTED,
        event_date=today_for(user),
        visibility=TimelineEvent.Visibility.PRIVATE,
        payload={
            "book_id": str(user_book.book_id),
            "title": user_book.book.title,
        },
    )
    return user_book


@transaction.atomic
def finish_reading(user_book: UserBook) -> UserBook:
    """Transiciona o UserBook para "lido" e propaga os efeitos de conclusão.

    Transição válida: apenas lendo -> lido. Efeitos, na mesma transação:
    finished_at (sem sobrescrever), current_page completado até o total do
    livro, bônus fixo de conclusão (R9, idempotente contra o ledger),
    TimelineEvent de BOOK_FINISHED e — se o bônus cruzar nível — LEVEL_UP.
    """
    # Relê a linha sob lock: sem isso, duas chamadas concorrentes passariam
    # ambas pela checagem de status E pela guarda de idempotência do bônus
    # (ler-comparar-escrever fora de lock), dobrando XP e eventos.
    user_book = UserBook.objects.select_for_update().get(pk=user_book.pk)

    if user_book.status != UserBook.Status.LENDO:
        raise ValidationError(
            f"Transição inválida: não é possível concluir um livro com "
            f"status {user_book.status!r} (esperado {UserBook.Status.LENDO.value!r})."
        )

    user = user_book.user
    today = today_for(user)
    user_book.status = UserBook.Status.LIDO
    if user_book.finished_at is None:
        user_book.finished_at = timezone.now()
    user_book.save(update_fields=["status", "finished_at"])

    # Conclusão implica ter chegado à última página — sem regredir avanço maior.
    UserBook.objects.filter(pk=user_book.pk).update(
        current_page=Greatest("current_page", Value(user_book.book.total_pages)),
    )
    user_book.refresh_from_db(fields=["current_page"])

    # Bônus R9: idempotente contra o LEDGER (imutável), não contra finished_at —
    # se o livro já rendeu o bônus alguma vez, nunca concede de novo.
    bonus_already_granted = XPTransaction.objects.filter(
        reason=XPTransaction.Reason.BOOK_COMPLETED,
        source_type="user_book",
        source_id=user_book.id,
    ).exists()
    if not bonus_already_granted:
        grant_result = grant_xp(
            user,
            BOOK_COMPLETED_XP,
            XPTransaction.Reason.BOOK_COMPLETED,
            source_type="user_book",
            source_id=user_book.id,
        )
        # Level-up gera evento na timeline onde quer que o XP tenha sido
        # concedido — inclusive pelo bônus de conclusão.
        if grant_result.level_changed:
            create_level_up_event(user, grant_result, today)

    TimelineEvent.objects.create(
        user=user,
        type=TimelineEvent.Type.BOOK_FINISHED,
        event_date=today,
        visibility=TimelineEvent.Visibility.PRIVATE,
        payload={
            "book_id": str(user_book.book_id),
            "title": user_book.book.title,
        },
    )

    # Livro concluído conta para quests/achievements de books_finished.
    handle_book_finished(user)

    return user_book
