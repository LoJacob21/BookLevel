"""Testes da API v1 (fim-a-fim, via HTTP com o test client do Django).

Foco: contrato HTTP (códigos, corpo, escopo por user, revogação de token).
A lógica de domínio tem suíte própria nos apps — aqui basta um assert de
integração no ledger para provar que a casca chama o service de verdade.
"""

import json
from datetime import date
from unittest import mock

from django.test import TestCase

from accounts.models import ApiToken, User
from accounts.services import deactivate_account
from catalog.models import Book
from gamification.models import Level, XPTransaction
from library.models import UserBook

API = "/api/v1"


def make_user(email="reader@example.com", nickname="reader"):
    return User.objects.create_user(
        email=email,
        nickname=nickname,
        password="senha-forte",
        timezone="America/Sao_Paulo",
    )


def make_token(user) -> str:
    return ApiToken.objects.create(user=user).key


class ApiTestCase(TestCase):
    """Helpers de request JSON com Authorization: Bearer opcional."""

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"} if token else {}

    def api_get(self, path, token=None):
        return self.client.get(API + path, headers=self._headers(token))

    def api_post(self, path, payload=None, token=None):
        return self.client.post(
            API + path,
            data=json.dumps(payload) if payload is not None else None,
            content_type="application/json",
            headers=self._headers(token),
        )

    def api_delete(self, path, token=None):
        return self.client.delete(API + path, headers=self._headers(token))


class AuthFlowTests(ApiTestCase):
    def test_full_flow_register_login_me_logout_token_revoked(self):
        response = self.api_post(
            "/auth/register",
            {"email": "leitora@example.com", "nickname": "leitora", "password": "senha-forte"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["email"], "leitora@example.com")
        self.assertEqual(body["nickname"], "leitora")

        response = self.api_post(
            "/auth/login", {"email": "leitora@example.com", "password": "senha-forte"}
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]

        response = self.api_get("/me", token=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["nickname"], "leitora")

        response = self.api_post("/auth/logout", token=token)
        self.assertEqual(response.status_code, 204)

        # O mesmo token não vale mais depois do logout.
        self.assertEqual(self.api_get("/me", token=token).status_code, 401)

    def test_wrong_credentials_rejected(self):
        make_user()
        response = self.api_post(
            "/auth/login", {"email": "reader@example.com", "password": "errada"}
        )
        self.assertEqual(response.status_code, 401)

    def test_missing_or_invalid_token_rejected(self):
        self.assertEqual(self.api_get("/me").status_code, 401)
        self.assertEqual(self.api_get("/me", token="f" * 64).status_code, 401)

    def test_inactive_user_rejected_even_with_valid_token(self):
        user = make_user()
        token = make_token(user)
        deactivate_account(user)  # soft-delete direto no service, token intacto

        self.assertEqual(self.api_get("/me", token=token).status_code, 401)

    def test_delete_account_revokes_all_tokens(self):
        user = make_user()
        token1, token2 = make_token(user), make_token(user)

        response = self.api_delete("/auth/account", token=token1)
        self.assertEqual(response.status_code, 204)

        # AMBOS os tokens morrem, não só o da request.
        self.assertEqual(self.api_get("/me", token=token1).status_code, 401)
        self.assertEqual(self.api_get("/me", token=token2).status_code, 401)
        self.assertEqual(ApiToken.objects.filter(user=user).count(), 0)
        user.refresh_from_db()
        self.assertFalse(user.is_active)


class RegisterTests(ApiTestCase):
    def test_duplicate_email_returns_400(self):
        make_user(email="dup@example.com", nickname="original")
        response = self.api_post(
            "/auth/register",
            {"email": "dup@example.com", "nickname": "outra", "password": "senha-forte"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["detail"])

    def test_duplicate_nickname_returns_400(self):
        make_user(email="a@example.com", nickname="dup")
        response = self.api_post(
            "/auth/register",
            {"email": "b@example.com", "nickname": "DUP", "password": "senha-forte"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nickname", response.json()["detail"])

    def test_invalid_iana_timezone_returns_422(self):
        response = self.api_post(
            "/auth/register",
            {
                "email": "tz@example.com",
                "nickname": "tz",
                "password": "senha-forte",
                "timezone": "Marte/Cratera",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertFalse(User.objects.filter(email="tz@example.com").exists())

    def test_valid_timezone_is_persisted(self):
        response = self.api_post(
            "/auth/register",
            {
                "email": "tz@example.com",
                "nickname": "tz",
                "password": "senha-forte",
                "timezone": "America/Sao_Paulo",
            },
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="tz@example.com")
        self.assertEqual(user.timezone, "America/Sao_Paulo")


class LibraryScopeTests(ApiTestCase):
    """User A nunca vê/transiciona/escreve no UserBook do user B — 404 sempre."""

    def setUp(self):
        self.user_a = make_user(email="a@example.com", nickname="user-a")
        self.token_a = make_token(self.user_a)
        user_b = make_user(email="b@example.com", nickname="user-b")
        book = Book.objects.create(title="Livro de B", total_pages=300)
        self.user_book_b = UserBook.objects.create(
            user=user_b, book=book, status=UserBook.Status.LENDO,
        )

    def test_listing_does_not_include_foreign_userbook(self):
        response = self.api_get("/library", token=self.token_a)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_foreign_sessions_listing_is_404(self):
        response = self.api_get(
            f"/library/{self.user_book_b.id}/sessions", token=self.token_a
        )
        self.assertEqual(response.status_code, 404)

    def test_foreign_transitions_are_404(self):
        for action in ("start", "finish"):
            response = self.api_post(
                f"/library/{self.user_book_b.id}/{action}", token=self.token_a
            )
            self.assertEqual(response.status_code, 404, action)
        # Nada mudou no UserBook de B.
        self.user_book_b.refresh_from_db()
        self.assertEqual(self.user_book_b.status, UserBook.Status.LENDO)

    def test_foreign_session_registration_is_404(self):
        response = self.api_post(
            f"/library/{self.user_book_b.id}/sessions",
            {"start_page": 0, "end_page": 10, "occurred_on": "2026-07-01"},
            token=self.token_a,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(XPTransaction.objects.count(), 0)


class LibraryBehaviorTests(ApiTestCase):
    def setUp(self):
        self.user = make_user()
        self.token = make_token(self.user)
        self.book = Book.objects.create(title="Livro", total_pages=300)
        self.user_book = UserBook.objects.create(
            user=self.user, book=self.book, status=UserBook.Status.LENDO,
        )
        Level.objects.create(level_number=1, xp_required=0, title="L1")
        Level.objects.create(level_number=2, xp_required=100000, title="L2")

    def test_add_duplicate_book_returns_400(self):
        other = Book.objects.create(title="Outro", total_pages=100)

        response = self.api_post("/library", {"book_id": str(other.id)}, token=self.token)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], UserBook.Status.QUERO_LER)

        response = self.api_post("/library", {"book_id": str(other.id)}, token=self.token)
        self.assertEqual(response.status_code, 400)
        self.assertIn("biblioteca", response.json()["detail"])

    def test_invalid_transition_returns_400_with_detail(self):
        # LENDO -> start é transição inválida (ValidationError do service).
        response = self.api_post(
            f"/library/{self.user_book.id}/start", token=self.token
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Transição inválida", response.json()["detail"])

    def test_register_session_propagates_to_ledger(self):
        response = self.api_post(
            f"/library/{self.user_book.id}/sessions",
            {
                "start_page": 0,
                "end_page": 50,
                "duration_minutes": 30,
                "occurred_on": "2026-07-01",
            },
            token=self.token,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["start_page"], 0)
        self.assertEqual(body["end_page"], 50)
        self.assertEqual(body["occurred_on"], "2026-07-01")

        # Um assert de integração no ledger prova que a casca chamou o
        # service de verdade — os efeitos completos têm suíte própria.
        self.assertTrue(
            XPTransaction.objects.filter(
                user=self.user,
                reason=XPTransaction.Reason.PAGES_READ,
                amount=50,
            ).exists()
        )

    def test_finish_twice_second_is_400_and_bonus_is_single(self):
        response = self.api_post(
            f"/library/{self.user_book.id}/finish", token=self.token
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], UserBook.Status.LIDO)

        response = self.api_post(
            f"/library/{self.user_book.id}/finish", token=self.token
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

        # Guarda anti-duplicação: o ledger fica com UM único bônus.
        self.assertEqual(
            XPTransaction.objects.filter(
                reason=XPTransaction.Reason.BOOK_COMPLETED
            ).count(),
            1,
        )


@mock.patch("api.v1.catalog.fetch_and_import_book")
class CatalogSearchTests(ApiTestCase):
    """Bloco C: contrato do GET /books com o gatilho de busca externa.

    A task é mockada na namespace do endpoint (api.v1.catalog): aqui só
    interessa SE e COMO o disparo acontece — a importação real tem suíte
    própria em catalog/tests.py.
    """

    def setUp(self):
        self.token = ApiToken.objects.create(user=make_user()).key

    def test_local_hit_does_not_trigger_external_search(self, mock_task):
        Book.objects.create(title="O Hobbit", total_pages=310)

        response = self.api_get("/books?q=Hobbit", token=self.token)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["external_search_triggered"])
        self.assertEqual(body["count"], 1)
        mock_task.delay.assert_not_called()

    def test_local_miss_triggers_task_and_returns_flag_true(self, mock_task):
        response = self.api_get("/books?q=inexistente", token=self.token)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["external_search_triggered"])
        self.assertEqual(body["items"], [])
        self.assertEqual(body["count"], 0)
        mock_task.delay.assert_called_once_with("inexistente")

    def test_empty_or_whitespace_query_never_triggers(self, mock_task):
        for path in ("/books", "/books?q=", "/books?q=%20%20%20"):
            response = self.api_get(path, token=self.token)
            self.assertEqual(response.status_code, 200, path)
            self.assertFalse(response.json()["external_search_triggered"], path)
        mock_task.delay.assert_not_called()

    def test_broker_failure_returns_200_with_flag_false_and_warning(self, mock_task):
        mock_task.delay.side_effect = Exception("broker fora do ar")

        with self.assertLogs("api.v1.catalog", level="WARNING"):
            response = self.api_get("/books?q=inexistente", token=self.token)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["external_search_triggered"])

    def test_page_zero_is_422(self, mock_task):
        response = self.api_get("/books?q=x&page=0", token=self.token)

        self.assertEqual(response.status_code, 422)

    def test_pagination_is_deterministic_with_duplicate_titles(self, mock_task):
        # 25 títulos idênticos: sem desempate por id, a composição das
        # páginas seria indefinida (itens repetidos/omitidos entre páginas).
        created_ids = {
            str(Book.objects.create(title="Mesmo Título", total_pages=100).id)
            for _ in range(25)
        }

        seen_ids = []
        for page in (1, 2):
            body = self.api_get(f"/books?q=Mesmo&page={page}", token=self.token).json()
            self.assertEqual(body["count"], 25)
            seen_ids.extend(item["id"] for item in body["items"])

        self.assertEqual(len(seen_ids), 25)  # 20 + 5, nada omitido
        self.assertEqual(len(set(seen_ids)), 25)  # nada repetido
        self.assertEqual(set(seen_ids), created_ids)
        mock_task.delay.assert_not_called()

    def test_response_envelope_has_exactly_the_contract_keys(self, mock_task):
        response = self.api_get("/books", token=self.token)

        self.assertEqual(
            set(response.json()), {"items", "count", "external_search_triggered"}
        )
