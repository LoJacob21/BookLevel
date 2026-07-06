"""Endpoints do catálogo (montados sob /books). Somente leitura no MVP."""

import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.pagination import PageNumberPagination, paginate

from catalog.models import Book

router = Router(tags=["catalog"])


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


@router.get("", response=list[BookOut])
@paginate(PageNumberPagination, page_size=20)
def search_books(request, q: str = ""):
    """Busca no catálogo LOCAL por título/autor (icontains), paginada."""
    books = Book.objects.prefetch_related("authors").order_by("title")
    if q:
        # TODO(Onda 3 — Bloco 2): este é o ponto exato do fallback para a
        # busca externa (Open Library) via Celery, importando o resultado
        # para o catálogo local antes de responder.
        books = books.filter(
            Q(title__icontains=q) | Q(authors__name__icontains=q)
        ).distinct()
    return books


@router.get("/{book_id}", response=BookDetailOut)
def get_book(request, book_id: uuid.UUID):
    return get_object_or_404(
        Book.objects.prefetch_related("authors", "genres"), pk=book_id
    )
