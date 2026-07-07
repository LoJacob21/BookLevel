"""Endpoints do catálogo (montados sob /books). Somente leitura no MVP.

GET /books tem envelope próprio (BookSearchOut) em vez do @paginate: a
resposta carrega external_search_triggered, que depende de estado da view
(houve miss? disparou a task?) e não cabe no envelope fixo da paginação.
items/count preservam nome, semântica e page_size do PageNumberPagination.
"""

import logging
import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema

from catalog.models import Book
from catalog.tasks import fetch_and_import_book

logger = logging.getLogger(__name__)

router = Router(tags=["catalog"])

PAGE_SIZE = 20


class AuthorOut(Schema):
    id: uuid.UUID
    name: str


class GenreOut(Schema):
    id: uuid.UUID
    name: str


class BookOut(Schema):
    id: uuid.UUID
    title: str
    subtitle: str
    total_pages: int
    cover_url: str
    published_year: int | None
    authors: list[AuthorOut]


class BookDetailOut(BookOut):
    genres: list[GenreOut]
    source: str
    external_id: str | None


class BookSearchOut(Schema):
    items: list[BookOut]
    count: int
    # True somente quando esta request de fato enfileirou a busca externa —
    # sinal para o cliente re-buscar em instantes.
    external_search_triggered: bool


@router.get("", response=BookSearchOut)
def search_books(request, q: str = "", page: int = Query(1, ge=1)):
    """Busca no catálogo local por título/autor (icontains), paginada.

    A1: cache miss (q preenchido e zero matches locais) dispara a
    importação externa assíncrona e responde imediato com o que há —
    nunca bloqueia esperando as fontes.
    """
    # q só de whitespace não filtra nem dispara busca externa (não queimar
    # cota das fontes com string vazia na prática).
    q = q.strip()
    # "id" desempata títulos iguais: composição das páginas determinística.
    books = Book.objects.prefetch_related("authors").order_by("title", "id")
    if q:
        books = books.filter(
            Q(title__icontains=q) | Q(authors__name__icontains=q)
        ).distinct()

    # Count do queryset filtrado completo (não da página): é ele que define
    # o miss e o total que o cliente usa para paginar.
    count = books.count()

    external_search_triggered = False
    if q and count == 0:
        # O gatilho assíncrono NUNCA pode derrubar a busca local: broker
        # fora do ar levanta erro de conexão no .delay() (kombu
        # OperationalError, erros do client redis, ...) — Exception largo
        # de propósito, porque qualquer falha aqui tem a mesma resposta
        # honesta: warning + flag false (nada foi disparado, o cliente não
        # deve esperar novidade ao re-buscar).
        try:
            fetch_and_import_book.delay(q)
            external_search_triggered = True
        except Exception:
            logger.warning(
                "broker indisponível; busca externa não disparada para %r",
                q,
                exc_info=True,
            )

    offset = (page - 1) * PAGE_SIZE
    return {
        "items": books[offset : offset + PAGE_SIZE],
        "count": count,
        "external_search_triggered": external_search_triggered,
    }


@router.get("/{book_id}", response=BookDetailOut)
def get_book(request, book_id: uuid.UUID):
    # R2: nunca enriquece — serve só o catálogo local.
    return get_object_or_404(
        Book.objects.prefetch_related("authors", "genres"), pk=book_id
    )
