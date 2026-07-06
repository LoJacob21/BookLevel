"""Endpoints da biblioteca pessoal (montados sob /library).

Tudo escopado ao request.auth: o user NUNCA vem do payload, e todo lookup
de UserBook filtra user=request.auth — registro inexistente e registro de
outro user respondem o MESMO 404, sem vazar existência.

Cascas finas: transições e efeitos (XP, streak, quests, timeline) vivem
nos services de library; aqui só entrada validada e saída serializada.
"""

import uuid
from datetime import date, datetime

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from ninja import Field, Router, Schema
from ninja.errors import HttpError
from ninja.pagination import PageNumberPagination, paginate
from pydantic import model_validator

from catalog.models import Book
from library.models import UserBook
from library.services import finish_reading, register_reading_session, start_reading

from .catalog import BookOut

router = Router(tags=["library"])


class UserBookOut(Schema):
    id: uuid.UUID
    book: BookOut
    status: str
    current_page: int
    started_at: datetime | None
    finished_at: datetime | None


class UserBookIn(Schema):
    book_id: uuid.UUID


class SessionIn(Schema):
    # ge=0 espelha os PositiveIntegerField; end >= start espelha a
    # ck_session_pages — validação de ENTRADA (422), não regra de negócio.
    start_page: int = Field(ge=0)
    end_page: int = Field(ge=0)
    duration_minutes: int | None = Field(default=None, ge=0)
    occurred_on: date

    @model_validator(mode="after")
    def _paginas_em_ordem(self):
        if self.end_page < self.start_page:
            raise ValueError("end_page não pode ser menor que start_page")
        return self


class SessionOut(Schema):
    id: uuid.UUID
    start_page: int
    end_page: int
    duration_minutes: int | None
    occurred_on: date
    created_at: datetime


def _get_user_book(request, user_book_id: uuid.UUID) -> UserBook:
    # SEMPRE filtrando user=request.auth: 404 (não 403) para não vazar a
    # existência de UserBooks alheios.
    return get_object_or_404(
        UserBook.objects.select_related("book"),
        pk=user_book_id,
        user=request.auth,
    )


@router.get("", response=list[UserBookOut])
def list_library(request, status: str | None = None):
    user_books = (
        UserBook.objects.filter(user=request.auth)
        .select_related("book")
        .prefetch_related("book__authors")
        .order_by("book__title")
    )
    if status is not None:
        if status not in UserBook.Status.values:
            raise HttpError(
                400, f"status inválido: {status!r}. Use um de {UserBook.Status.values}."
            )
        user_books = user_books.filter(status=status)
    return user_books


@router.post("", response={201: UserBookOut})
def add_to_library(request, payload: UserBookIn):
    book = get_object_or_404(Book, pk=payload.book_id)
    try:
        # atomic DENTRO do try: o savepoint isola o IntegrityError e não
        # envenena a transação externa (mesmo racional dos get_or_create
        # do StreakFreeze e da auto-inscrição de quests).
        with transaction.atomic():
            user_book = UserBook.objects.create(
                user=request.auth, book=book, status=UserBook.Status.QUERO_LER,
            )
    except IntegrityError:
        # uq_userbook_user_book: um livro por usuário.
        raise HttpError(400, "Este livro já está na sua biblioteca.")
    return 201, user_book


@router.post("/{user_book_id}/start", response=UserBookOut)
def start(request, user_book_id: uuid.UUID):
    user_book = _get_user_book(request, user_book_id)
    # Transição inválida -> ValidationError do service -> 400 (handler global).
    return start_reading(user_book)


@router.post("/{user_book_id}/finish", response=UserBookOut)
def finish(request, user_book_id: uuid.UUID):
    user_book = _get_user_book(request, user_book_id)
    return finish_reading(user_book)


@router.post("/{user_book_id}/sessions", response={201: SessionOut})
def create_session(request, user_book_id: uuid.UUID, payload: SessionIn):
    user_book = _get_user_book(request, user_book_id)
    session = register_reading_session(
        user_book,
        start_page=payload.start_page,
        end_page=payload.end_page,
        duration_minutes=payload.duration_minutes,
        occurred_on=payload.occurred_on,
    )
    return 201, session


@router.get("/{user_book_id}/sessions", response=list[SessionOut])
@paginate(PageNumberPagination, page_size=20)
def list_sessions(request, user_book_id: uuid.UUID):
    user_book = _get_user_book(request, user_book_id)
    return user_book.sessions.order_by("-occurred_on", "-created_at")
