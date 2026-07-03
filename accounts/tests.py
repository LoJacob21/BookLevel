"""Testes de deactivate_account() (soft-delete, R10)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from gamification.models import Level, XPTransaction
from gamification.services import grant_xp
from timeline.models import TimelineEvent

from .services import deactivate_account

User = get_user_model()


class DeactivateAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="reader@example.com",
            nickname="reader",
            password="x",
            timezone="America/Sao_Paulo",
            bio="Leitora ávida.",
        )
        Level.objects.create(level_number=1, xp_required=0, title="L1")
        Level.objects.create(level_number=2, xp_required=1000, title="L2")

    def test_anonymizes_login_fields(self):
        deactivate_account(self.user)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertRegex(
            self.user.email, r"^deleted-[0-9a-f-]{36}@anonymized\.invalid$"
        )
        self.assertRegex(self.user.nickname, r"^deleted-[0-9a-f]{12}$")
        self.assertEqual(self.user.bio, "")

    def test_touches_nothing_beyond_the_four_fields(self):
        self.user.total_xp = 500
        self.user.current_level = 1
        self.user.save(update_fields=["total_xp", "current_level"])

        deactivate_account(self.user)

        self.user.refresh_from_db()
        # Campos fora do escopo do soft-delete permanecem intactos.
        self.assertEqual(self.user.timezone, "America/Sao_Paulo")
        self.assertEqual(self.user.total_xp, 500)
        self.assertEqual(self.user.current_level, 1)

    def test_ledger_and_timeline_survive_deactivation(self):
        grant_xp(
            self.user, 50, XPTransaction.Reason.PAGES_READ,
            source_type="reading_session",
        )
        TimelineEvent.objects.create(
            user=self.user,
            type=TimelineEvent.Type.STREAK_KEPT,
            event_date=date(2026, 7, 1),
            payload={},
        )

        deactivate_account(self.user)

        # Histórico preservado (R10): ledger e timeline continuam ligados ao user.
        self.assertEqual(XPTransaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(TimelineEvent.objects.filter(user=self.user).count(), 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_xp, 50)

    def test_two_deactivated_accounts_do_not_collide(self):
        other = User.objects.create_user(
            email="other@example.com",
            nickname="other",
            password="x",
            timezone="America/Sao_Paulo",
        )

        deactivate_account(self.user)
        deactivate_account(other)  # não pode violar unique de email/nickname

        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertNotEqual(self.user.email, other.email)
        self.assertNotEqual(self.user.nickname, other.nickname)
