"""Testes da importação externa do catálogo (service + task).

Nenhuma chamada HTTP real: o I/O é mockado no ponto mais baixo —
catalog.external.requests.get — para que a tradução dos payloads de cada
fonte também seja exercitada. Com CELERY_TASK_ALWAYS_EAGER, .delay()
executa a task inline.
"""

from unittest import mock

import requests
from celery.exceptions import Retry
from django.test import TestCase

from catalog import external
from catalog.models import Author, Book, Genre
from catalog.services import import_book
from catalog.tasks import fetch_and_import_book

# ISBN-13 e o ISBN-10 equivalente (mesmo corpo, dígito verificador próprio).
ISBN13 = "9780306406157"
ISBN10 = "0306406152"


def make_external_payload(**overrides):
    """Payload no formato interno comum de catalog.external."""
    data = {
        "title": "O Hobbit",
        "subtitle": "",
        "total_pages": 310,
        "cover_url": "",
        "published_year": 1937,
        "isbns": [ISBN13],
        "source": Book.Source.OPEN_LIBRARY,
        "external_id": "/works/OL45883W",
        "authors": ["J. R. R. Tolkien"],
    }
    data.update(overrides)
    return data


# Payloads crus como as APIs de verdade respondem (o client traduz).
OPEN_LIBRARY_PAYLOAD = {
    "docs": [
        {
            "key": "/works/OL45883W",
            "title": "O Hobbit",
            "subtitle": "",
            "author_name": ["J. R. R. Tolkien"],
            "first_publish_year": 1937,
            "number_of_pages_median": 310,
            "isbn": ["978-0-306-40615-7"],
            "cover_i": 12345,
        }
    ]
}

GOOGLE_BOOKS_PAYLOAD = {
    "items": [
        {
            "id": "gb-abc123",
            "volumeInfo": {
                "title": "O Hobbit",
                "authors": ["J. R. R. Tolkien"],
                "publishedDate": "1937-09-21",
                "pageCount": 310,
                "industryIdentifiers": [
                    {"type": "ISBN_13", "identifier": ISBN13},
                ],
                "imageLinks": {"thumbnail": "http://books.google.com/thumb.jpg"},
            },
        }
    ]
}


def fake_response(payload):
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def fake_http(open_library=None, google_books=None):
    """side_effect para requests.get: payload dict, exceção, ou None (falha
    do teste se a fonte for chamada sem resposta prevista)."""

    def _get(url, **kwargs):
        by_url = {
            external.OPEN_LIBRARY_SEARCH_URL: open_library,
            external.GOOGLE_BOOKS_SEARCH_URL: google_books,
        }
        if url not in by_url:
            raise AssertionError(f"URL inesperada: {url}")
        outcome = by_url[url]
        if outcome is None:
            raise AssertionError(f"fonte não deveria ter sido chamada: {url}")
        if isinstance(outcome, Exception):
            raise outcome
        return fake_response(outcome)

    return _get


class ImportBookTests(TestCase):
    """Bloco A: dedup em cascata e regras de importação do service."""

    def test_existing_isbn13_returns_existing_book(self):
        existing = Book.objects.create(
            title="O Hobbit (local)", total_pages=300, isbn=ISBN13,
        )
        # Mesmo ISBN vindo de OUTRA fonte, com external_id diferente.
        imported = import_book(make_external_payload(external_id="/works/OUTRO"))

        self.assertEqual(imported.id, existing.id)
        self.assertEqual(Book.objects.count(), 1)
        # R3: o existente fica intocado (sem re-enriquecimento).
        existing.refresh_from_db()
        self.assertEqual(existing.title, "O Hobbit (local)")

    def test_isbn10_converts_and_dedups_against_local_isbn13(self):
        existing = Book.objects.create(
            title="O Hobbit", total_pages=300, isbn=ISBN13,
        )
        imported = import_book(
            make_external_payload(isbns=["0-306-40615-2"])  # só o ISBN-10, com hífens
        )

        self.assertEqual(imported.id, existing.id)
        self.assertEqual(Book.objects.count(), 1)

    def test_no_isbn_dedups_by_source_and_external_id(self):
        existing = Book.objects.create(
            title="Sem ISBN",
            total_pages=200,
            source=Book.Source.OPEN_LIBRARY,
            external_id="/works/OL1W",
        )
        imported = import_book(
            make_external_payload(isbns=[], external_id="/works/OL1W")
        )

        self.assertEqual(imported.id, existing.id)
        self.assertEqual(Book.objects.count(), 1)

    def test_existing_author_is_reused_case_insensitive(self):
        author = Author.objects.create(
            name="J. R. R. Tolkien", normalized_name="J. R. R. Tolkien"
        )
        # Caixa diferente + espaço duplo: normalize colapsa, iexact casa.
        book = import_book(make_external_payload(authors=["j. r. r.  TOLKIEN"]))

        self.assertEqual(Author.objects.count(), 1)
        self.assertEqual(list(book.authors.all()), [author])

    def test_new_author_is_created(self):
        book = import_book(make_external_payload(authors=["Ursula K. Le Guin"]))

        author = Author.objects.get()
        self.assertEqual(author.name, "Ursula K. Le Guin")
        self.assertEqual(author.normalized_name, "Ursula K. Le Guin")
        self.assertEqual(list(book.authors.all()), [author])

    def test_missing_total_pages_imports_as_zero(self):
        book = import_book(make_external_payload(total_pages=None))

        book.refresh_from_db()  # prova que o CHECK >= 0 aceitou
        self.assertEqual(book.total_pages, 0)

    def test_import_never_creates_genre(self):
        import_book(make_external_payload())

        self.assertEqual(Genre.objects.count(), 0)

    def test_absent_isbn_and_external_id_persist_as_none_not_empty_string(self):
        # Regressão da regra "" -> None: string vazia entraria nas uniques
        # parciais (isnull=False) e faria dois livros sem ISBN colidirem.
        book = import_book(
            make_external_payload(isbns=[], external_id="", title="Sem nada")
        )

        book.refresh_from_db()
        self.assertIsNone(book.isbn)
        self.assertIsNone(book.external_id)


class FetchAndImportBookTaskTests(TestCase):
    """Bloco B: cascata de fontes e retry da task (HTTP mockado)."""

    def test_open_library_hit_imports_without_calling_google_books(self):
        with mock.patch(
            "catalog.external.requests.get",
            side_effect=fake_http(open_library=OPEN_LIBRARY_PAYLOAD),
        ) as mock_get:
            fetch_and_import_book.delay("hobbit")

        book = Book.objects.get()
        self.assertEqual(book.source, Book.Source.OPEN_LIBRARY)
        self.assertEqual(book.external_id, "/works/OL45883W")
        self.assertEqual(book.isbn, ISBN13)
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertNotIn(external.GOOGLE_BOOKS_SEARCH_URL, called_urls)
        # R9: toda chamada externa se identifica.
        for call in mock_get.call_args_list:
            self.assertEqual(call.kwargs["headers"]["User-Agent"], external.USER_AGENT)

    def test_open_library_error_falls_back_to_google_books(self):
        with mock.patch(
            "catalog.external.requests.get",
            side_effect=fake_http(
                open_library=requests.ConnectionError("fora do ar"),
                google_books=GOOGLE_BOOKS_PAYLOAD,
            ),
        ):
            fetch_and_import_book.delay("hobbit")

        book = Book.objects.get()
        self.assertEqual(book.source, Book.Source.GOOGLE_BOOKS)
        self.assertEqual(book.external_id, "gb-abc123")

    def test_open_library_empty_falls_back_to_google_books(self):
        with mock.patch(
            "catalog.external.requests.get",
            side_effect=fake_http(
                open_library={"docs": []},
                google_books=GOOGLE_BOOKS_PAYLOAD,
            ),
        ):
            fetch_and_import_book.delay("hobbit")

        book = Book.objects.get()
        self.assertEqual(book.source, Book.Source.GOOGLE_BOOKS)

    def test_both_sources_empty_ends_quietly_without_creating_book(self):
        with mock.patch(
            "catalog.external.requests.get",
            side_effect=fake_http(open_library={"docs": []}, google_books={}),
        ):
            fetch_and_import_book.delay("livro inexistente")  # não pode levantar

        self.assertEqual(Book.objects.count(), 0)

    def test_transient_failure_invokes_retry_with_bounded_countdown(self):
        retries = 2
        with (
            mock.patch(
                "catalog.external.requests.get",
                side_effect=fake_http(
                    open_library=requests.Timeout("timeout"),
                    google_books=requests.ConnectionError("fora do ar"),
                ),
            ),
            mock.patch.object(
                fetch_and_import_book, "retry", side_effect=Retry("retry!")
            ) as mock_retry,
        ):
            # push_request injeta self.request.retries sem broker de verdade;
            # .run() executa o corpo da task com o self real.
            fetch_and_import_book.push_request(retries=retries)
            try:
                with self.assertRaises(Retry):
                    fetch_and_import_book.run("hobbit")
            finally:
                fetch_and_import_book.pop_request()

        mock_retry.assert_called_once()
        countdown = mock_retry.call_args.kwargs["countdown"]
        self.assertGreaterEqual(countdown, 0)
        self.assertLessEqual(countdown, min(2**retries, 60))
        self.assertEqual(Book.objects.count(), 0)
