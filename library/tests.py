"""Testes de integração de register_reading_session()."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from catalog.models import Book
from gamification.models import Level, Streak, XPTransaction
from timeline.models import TimelineEvent

from .models import ReadingSession, UserBook
from .services import register_reading_session

User = get_user_model()

# Tipos de evento que este service NUNCA pode criar (pertencem a outros services).
FORBIDDEN_EVENT_TYPES = [
    TimelineEvent.Type.BOOK_STARTED,
    TimelineEvent.Type.BOOK_FINISHED,
    TimelineEvent.Type.QUEST_COMPLETED,
    TimelineEvent.Type.ACHIEVEMENT_UNLOCKED,
]


def make_user(email="reader@example.com", nickname="reader"):
    return User.objects.create_user(
        email=email,
        nickname=nickname,
        password="x",
        timezone="America/Sao_Paulo",
    )


class RegisterReadingSessionTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.book = Book.objects.create(title="Livro", total_pages=300)
        self.user_book = UserBook.objects.create(
            user=self.user,
            book=self.book,
            status=UserBook.Status.LENDO,
            current_page=0,
        )
        self.d1 = date(2026, 7, 1)
        # Níveis padrão (afastados) — a maioria dos testes não deve subir de nível.
        Level.objects.create(level_number=1, xp_required=0, title="L1")
        Level.objects.create(level_number=2, xp_required=1000, title="L2")

    # --- C1: validação + atomicidade -----------------------------------

    def test_end_page_beyond_total_raises_and_no_side_effects(self):
        with self.assertRaises(ValidationError):
            register_reading_session(
                self.user_book,
                start_page=0,
                end_page=400,  # > total_pages (300)
                duration_minutes=10,
                occurred_on=self.d1,
            )

        self.assertEqual(ReadingSession.objects.count(), 0)
        self.assertEqual(XPTransaction.objects.count(), 0)
        self.assertEqual(TimelineEvent.objects.count(), 0)
        self.user_book.refresh_from_db()
        self.assertEqual(self.user_book.current_page, 0)

    # --- C2/C3: current_page nunca regride ------------------------------

    def test_chronological_session_advances_current_page(self):
        self.user_book.current_page = 10
        self.user_book.save()

        register_reading_session(
            self.user_book, start_page=10, end_page=50,
            duration_minutes=5, occurred_on=self.d1,
        )

        self.user_book.refresh_from_db()
        self.assertEqual(self.user_book.current_page, 50)

    def test_retroactive_session_does_not_regress_current_page(self):
        self.user_book.current_page = 100
        self.user_book.save()

        register_reading_session(
            self.user_book, start_page=10, end_page=50,
            duration_minutes=5, occurred_on=self.d1,
        )

        self.user_book.refresh_from_db()
        self.assertEqual(self.user_book.current_page, 100)  # não regrediu

    # --- C4/C5: TimelineEvent LEVEL_UP ----------------------------------

    def test_session_crossing_level_creates_level_up_event(self):
        # L2 exige 50 XP para este teste.
        Level.objects.filter(level_number=2).update(xp_required=50)

        register_reading_session(
            self.user_book, start_page=0, end_page=60,  # 60 XP
            duration_minutes=5, occurred_on=self.d1,
        )

        events = TimelineEvent.objects.filter(type=TimelineEvent.Type.LEVEL_UP)
        self.assertEqual(events.count(), 1)
        payload = events.get().payload
        self.assertEqual(payload["old_level"], 1)
        self.assertEqual(payload["new_level"], 2)
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_level, 2)

    def test_session_without_level_up_creates_no_level_event(self):
        register_reading_session(
            self.user_book, start_page=0, end_page=40,  # 40 XP, L2 = 1000
            duration_minutes=5, occurred_on=self.d1,
        )

        self.assertFalse(
            TimelineEvent.objects.filter(type=TimelineEvent.Type.LEVEL_UP).exists()
        )

    # --- C6/C7: TimelineEvent STREAK_KEPT -------------------------------

    def test_session_keeping_streak_creates_streak_event(self):
        # Ofensiva pré-existente terminando no dia anterior.
        Streak.objects.create(
            user=self.user, current_count=1, longest_count=1,
            last_active_on=self.d1,
        )
        d2 = self.d1 + timedelta(days=1)

        register_reading_session(
            self.user_book, start_page=0, end_page=10,
            duration_minutes=5, occurred_on=d2,
        )

        events = TimelineEvent.objects.filter(type=TimelineEvent.Type.STREAK_KEPT)
        self.assertEqual(events.count(), 1)
        payload = events.get().payload
        self.assertEqual(payload["current_count"], 2)
        self.assertEqual(payload["longest_count"], 2)

    def test_idempotent_streak_creates_no_streak_event(self):
        Streak.objects.create(
            user=self.user, current_count=1, longest_count=1,
            last_active_on=self.d1,
        )

        register_reading_session(
            self.user_book, start_page=0, end_page=10,
            duration_minutes=5, occurred_on=self.d1,  # mesmo dia
        )

        self.assertFalse(
            TimelineEvent.objects.filter(type=TimelineEvent.Type.STREAK_KEPT).exists()
        )

    # --- C8: pages_delta == 0 -------------------------------------------

    def test_zero_pages_delta_grants_no_xp(self):
        register_reading_session(
            self.user_book, start_page=50, end_page=50,  # delta 0
            duration_minutes=5, occurred_on=self.d1,
        )

        self.assertEqual(XPTransaction.objects.count(), 0)
        # A sessão em si é criada normalmente.
        self.assertEqual(ReadingSession.objects.count(), 1)

    # --- C9: nenhum tipo de evento proibido -----------------------------

    def test_no_forbidden_timeline_event_types_created(self):
        # Cenário rico: sobe de nível E mantém ofensiva.
        Level.objects.filter(level_number=2).update(xp_required=50)
        Streak.objects.create(
            user=self.user, current_count=1, longest_count=1,
            last_active_on=self.d1,
        )

        register_reading_session(
            self.user_book, start_page=0, end_page=60,
            duration_minutes=5, occurred_on=self.d1 + timedelta(days=1),
        )

        self.assertFalse(
            TimelineEvent.objects.filter(type__in=FORBIDDEN_EVENT_TYPES).exists()
        )
        # Só devem existir LEVEL_UP e STREAK_KEPT.
        created_types = set(
            TimelineEvent.objects.values_list("type", flat=True)
        )
        self.assertEqual(
            created_types,
            {TimelineEvent.Type.LEVEL_UP, TimelineEvent.Type.STREAK_KEPT},
        )

    # --- C10: ciclo de vida do UserBook intocado ------------------------

    def test_userbook_lifecycle_fields_never_change(self):
        started = self.user_book.started_at
        register_reading_session(
            self.user_book, start_page=0, end_page=60,
            duration_minutes=5, occurred_on=self.d1,
        )

        self.user_book.refresh_from_db()
        self.assertEqual(self.user_book.status, UserBook.Status.LENDO)
        self.assertEqual(self.user_book.started_at, started)
        self.assertIsNone(self.user_book.finished_at)
