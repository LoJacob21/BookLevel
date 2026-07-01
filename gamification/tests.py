"""Testes dos serviços de gamificação: grant_xp() e update_streak()."""

import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Level, Streak, StreakFreeze, XPTransaction
from .services import grant_xp, update_streak

User = get_user_model()


def make_user(email="reader@example.com", nickname="reader"):
    return User.objects.create_user(
        email=email,
        nickname=nickname,
        password="x",
        timezone="America/Sao_Paulo",
    )


class GrantXPTests(TestCase):
    def setUp(self):
        self.user = make_user()
        # Níveis ad-hoc — não depende do management command seed_levels.
        Level.objects.create(level_number=1, xp_required=0, title="L1")
        Level.objects.create(level_number=2, xp_required=100, title="L2")
        Level.objects.create(level_number=3, xp_required=150, title="L3")

    def test_grant_below_threshold_does_not_change_level(self):
        result = grant_xp(self.user, 50, XPTransaction.Reason.PAGES_READ)

        self.assertFalse(result.level_changed)
        self.assertEqual(result.new_level, 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_xp, 50)
        self.assertEqual(self.user.current_level, 1)

    def test_grant_crossing_one_level(self):
        result = grant_xp(self.user, 100, XPTransaction.Reason.PAGES_READ)

        self.assertTrue(result.level_changed)
        self.assertEqual(result.old_level, 1)
        self.assertEqual(result.new_level, 2)
        # Persistido no banco, não só no objeto Python.
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_level, 2)
        self.assertEqual(self.user.total_xp, 100)

    def test_grant_crossing_multiple_levels_lands_on_highest(self):
        result = grant_xp(self.user, 200, XPTransaction.Reason.PAGES_READ)

        # 200 XP passa de L2 (100) e L3 (150) — deve parar no MAIS ALTO.
        self.assertTrue(result.level_changed)
        self.assertEqual(result.new_level, 3)
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_level, 3)

    def test_grant_creates_xptransaction_with_correct_fields(self):
        source_id = uuid.uuid4()
        result = grant_xp(
            self.user,
            42,
            XPTransaction.Reason.PAGES_READ,
            source_type="reading_session",
            source_id=source_id,
        )

        self.assertEqual(XPTransaction.objects.count(), 1)
        txn = XPTransaction.objects.get()
        self.assertEqual(txn, result.transaction)
        self.assertEqual(txn.user, self.user)
        self.assertEqual(txn.amount, 42)
        self.assertEqual(txn.reason, XPTransaction.Reason.PAGES_READ)
        self.assertEqual(txn.source_type, "reading_session")
        self.assertEqual(txn.source_id, source_id)

    def test_invalid_reason_raises_and_leaves_no_effect(self):
        with self.assertRaises(ValueError):
            grant_xp(self.user, 50, "reason_invalido")

        # Atômico: nenhuma XPTransaction criada, total_xp intacto.
        self.assertEqual(XPTransaction.objects.count(), 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_xp, 0)
        self.assertEqual(self.user.current_level, 1)


class UpdateStreakTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.d1 = date(2026, 7, 1)

    def test_first_session_starts_streak(self):
        result = update_streak(self.user, self.d1)

        self.assertFalse(result.streak_kept)
        self.assertFalse(result.broke)
        self.assertFalse(result.freeze_used)
        self.assertEqual(result.current_count, 1)
        self.assertEqual(result.longest_count, 1)

        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 1)
        self.assertEqual(streak.longest_count, 1)
        self.assertEqual(streak.last_active_on, self.d1)

    def test_same_day_is_idempotent(self):
        update_streak(self.user, self.d1)
        result = update_streak(self.user, self.d1)

        self.assertFalse(result.streak_kept)
        self.assertFalse(result.broke)
        self.assertFalse(result.freeze_used)
        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 1)
        self.assertEqual(streak.longest_count, 1)
        self.assertEqual(streak.last_active_on, self.d1)

    def test_next_day_continues_streak(self):
        update_streak(self.user, self.d1)
        result = update_streak(self.user, self.d1 + timedelta(days=1))

        self.assertTrue(result.streak_kept)
        self.assertFalse(result.broke)
        self.assertFalse(result.freeze_used)
        self.assertEqual(result.current_count, 2)
        self.assertEqual(result.longest_count, 2)

        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 2)
        self.assertEqual(streak.longest_count, 2)
        self.assertEqual(streak.last_active_on, self.d1 + timedelta(days=1))

    def test_gap_with_freeze_available_preserves_streak(self):
        update_streak(self.user, self.d1)  # count = 1
        gap_day = self.d1 + timedelta(days=3)  # > d1 + 1
        result = update_streak(self.user, gap_day)

        self.assertTrue(result.streak_kept)
        self.assertTrue(result.freeze_used)
        self.assertFalse(result.broke)
        # current_count NÃO muda (freeze preserva).
        self.assertEqual(result.current_count, 1)

        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 1)
        self.assertEqual(streak.last_active_on, gap_day)

        # Um StreakFreeze foi criado para o period_key certo.
        freezes = StreakFreeze.objects.filter(user=self.user)
        self.assertEqual(freezes.count(), 1)
        self.assertEqual(freezes.get().period_key, "2026-07")
        self.assertEqual(freezes.get().used_on, gap_day)

    def test_gap_without_freeze_breaks_streak(self):
        # Constrói uma ofensiva e gasta o freeze do mês antes do 2º gap.
        update_streak(self.user, self.d1)                       # count 1
        update_streak(self.user, self.d1 + timedelta(days=1))   # count 2
        update_streak(self.user, self.d1 + timedelta(days=2))   # count 3
        update_streak(self.user, self.d1 + timedelta(days=4))   # gap -> freeze (count 3)

        second_gap = self.d1 + timedelta(days=6)  # mesmo mês, freeze já usado
        result = update_streak(self.user, second_gap)

        self.assertTrue(result.broke)
        self.assertFalse(result.streak_kept)
        self.assertFalse(result.freeze_used)
        self.assertEqual(result.current_count, 1)  # resetou (era 3)

        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 1)
        self.assertEqual(streak.longest_count, 3)  # inalterado
        self.assertEqual(streak.last_active_on, second_gap)
        # Só um freeze no mês inteiro.
        self.assertEqual(StreakFreeze.objects.filter(user=self.user).count(), 1)

    def test_retroactive_session_is_noop(self):
        update_streak(self.user, self.d1)                      # count 1
        update_streak(self.user, self.d1 + timedelta(days=1))  # count 2, last = d2

        result = update_streak(self.user, self.d1)  # occurred_on < last

        self.assertFalse(result.streak_kept)
        self.assertFalse(result.broke)
        self.assertFalse(result.freeze_used)
        self.assertEqual(result.current_count, 2)

        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 2)
        self.assertEqual(streak.longest_count, 2)
        self.assertEqual(streak.last_active_on, self.d1 + timedelta(days=1))

    def test_same_day_after_continuity_regression(self):
        # Ramo 3 (continuidade) seguido do ramo 2 (mesmo dia) — não isolado.
        update_streak(self.user, self.d1)                          # count 1
        d2 = self.d1 + timedelta(days=1)
        r_cont = update_streak(self.user, d2)                      # count 2, kept
        r_same = update_streak(self.user, d2)                      # mesmo dia

        self.assertTrue(r_cont.streak_kept)
        self.assertFalse(r_same.streak_kept)
        self.assertEqual(r_same.current_count, 2)

        streak = Streak.objects.get(user=self.user)
        self.assertEqual(streak.current_count, 2)
        self.assertEqual(streak.last_active_on, d2)
