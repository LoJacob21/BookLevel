"""Testes dos services de quests (progressão, conclusão, achievements).

Os handlers exigem transação ativa do caller (_require_atomic). O TestCase
do Django roda cada teste dentro de uma transação, então os handlers podem
ser chamados diretamente aqui — o mesmo contexto que têm em produção, onde
rodam dentro do atomic de register_reading_session/finish_reading.
"""

import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from catalog.models import Book
from gamification.models import Level, Streak, XPTransaction
from library.models import ReadingSession, UserBook
from timeline.models import TimelineEvent

from .models import Achievement, Quest, UserAchievement, UserQuest
from .services import handle_book_finished, handle_pages_read, handle_streak

User = get_user_model()


def make_user(email="reader@example.com", nickname="reader"):
    return User.objects.create_user(
        email=email,
        nickname=nickname,
        password="x",
        timezone="America/Sao_Paulo",
    )


def make_quest(code, criteria_type, criteria_value, **extra):
    return Quest.objects.create(
        code=code,
        name=code,
        criteria_type=criteria_type,
        criteria_value=criteria_value,
        **extra,
    )


class QuestServicesTestCase(TestCase):
    """Base comum: user + níveis afastados (nenhum teste deve subir de nível)."""

    def setUp(self):
        self.user = make_user()
        Level.objects.create(level_number=1, xp_required=0, title="L1")
        Level.objects.create(level_number=2, xp_required=100000, title="L2")


class AutoEnrollTests(QuestServicesTestCase):
    def test_first_relevant_event_enrolls_in_active_quest(self):
        quest = make_quest("q-pages", "pages_read", 100)

        handle_pages_read(self.user, 30)

        user_quest = UserQuest.objects.get(user=self.user, quest=quest)
        self.assertEqual(user_quest.status, UserQuest.Status.IN_PROGRESS)
        # Progresso conta o delta DESDE a inscrição — este primeiro evento entra.
        self.assertEqual(user_quest.progress_value, 30)

    def test_out_of_validity_window_does_not_enroll(self):
        now = timezone.now()
        make_quest("q-past", "pages_read", 100, valid_until=now - timedelta(days=1))
        make_quest("q-future", "pages_read", 100, valid_from=now + timedelta(days=1))

        handle_pages_read(self.user, 30)

        self.assertEqual(UserQuest.objects.count(), 0)

    def test_scoped_quest_does_not_enroll(self):
        make_quest("q-event", "pages_read", 100, event_id=uuid.uuid4())
        make_quest("q-community", "pages_read", 100, community_id=uuid.uuid4())

        handle_pages_read(self.user, 30)

        self.assertEqual(UserQuest.objects.count(), 0)

    def test_non_repeatable_already_played_never_reenrolls(self):
        quest = make_quest("q-once", "pages_read", 10, is_repeatable=False, xp_reward=5)

        handle_pages_read(self.user, 10)  # inscreve e completa
        handle_pages_read(self.user, 5)   # próximo evento relevante

        rows = UserQuest.objects.filter(user=self.user, quest=quest)
        self.assertEqual(rows.count(), 1)  # não nasceu segunda linha
        self.assertEqual(rows.get().status, UserQuest.Status.COMPLETED)

        # Guarda de regressão: se o filtro status=in_progress sumir de
        # _complete_reached_quests, a linha completed seria recompletada na
        # segunda chamada e dobraria XP/evento — são ESTES asserts que quebram.
        self.assertEqual(
            XPTransaction.objects.filter(
                reason=XPTransaction.Reason.QUEST_COMPLETED
            ).count(),
            1,
        )
        self.assertEqual(
            TimelineEvent.objects.filter(
                type=TimelineEvent.Type.QUEST_COMPLETED
            ).count(),
            1,
        )


class ProgressionTests(QuestServicesTestCase):
    def test_pages_read_accumulates_deltas(self):
        quest = make_quest("q-pages", "pages_read", 100)

        handle_pages_read(self.user, 30)
        handle_pages_read(self.user, 20)

        user_quest = UserQuest.objects.get(user=self.user, quest=quest)
        self.assertEqual(user_quest.progress_value, 50)
        self.assertEqual(user_quest.status, UserQuest.Status.IN_PROGRESS)

    def test_books_finished_increments_by_one(self):
        quest = make_quest("q-books", "books_finished", 3)

        handle_book_finished(self.user)
        handle_book_finished(self.user)

        user_quest = UserQuest.objects.get(user=self.user, quest=quest)
        self.assertEqual(user_quest.progress_value, 2)

    def test_streak_days_keeps_greatest_count(self):
        quest = make_quest("q-streak", "streak_days", 7)

        handle_streak(self.user, 3)
        handle_streak(self.user, 2)  # contagem menor (ex.: ofensiva reiniciada)

        user_quest = UserQuest.objects.get(user=self.user, quest=quest)
        self.assertEqual(user_quest.progress_value, 3)  # não regride

    def test_progress_may_exceed_criteria_value(self):
        # Delta que ultrapassa o alvo não é truncado (R4).
        quest = make_quest("q-pages", "pages_read", 50)

        handle_pages_read(self.user, 80)

        user_quest = UserQuest.objects.get(user=self.user, quest=quest)
        self.assertEqual(user_quest.progress_value, 80)


class CompletionTests(QuestServicesTestCase):
    def test_reaching_criteria_completes_quest_with_xp_and_event(self):
        quest = make_quest("q-pages", "pages_read", 50, xp_reward=40)

        handle_pages_read(self.user, 60)

        user_quest = UserQuest.objects.get(user=self.user, quest=quest)
        self.assertEqual(user_quest.status, UserQuest.Status.COMPLETED)
        self.assertIsNotNone(user_quest.completed_at)

        txn = XPTransaction.objects.get(reason=XPTransaction.Reason.QUEST_COMPLETED)
        self.assertEqual(txn.amount, 40)
        self.assertEqual(txn.source_type, "user_quest")
        self.assertEqual(txn.source_id, user_quest.id)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_xp, 40)

        events = TimelineEvent.objects.filter(type=TimelineEvent.Type.QUEST_COMPLETED)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().payload["code"], "q-pages")

    def test_zero_xp_reward_creates_event_but_no_transaction(self):
        make_quest("q-free", "pages_read", 50, xp_reward=0)

        handle_pages_read(self.user, 60)

        self.assertFalse(
            XPTransaction.objects.filter(
                reason=XPTransaction.Reason.QUEST_COMPLETED
            ).exists()
        )
        self.assertTrue(
            TimelineEvent.objects.filter(
                type=TimelineEvent.Type.QUEST_COMPLETED
            ).exists()
        )

    def test_repeatable_quest_reenrolls_lazily_on_next_event(self):
        quest = make_quest("q-repeat", "pages_read", 10, is_repeatable=True, xp_reward=5)

        handle_pages_read(self.user, 10)  # inscreve e completa a 1ª rodada

        # Lazy (R8): completar NÃO cria a próxima UserQuest imediatamente.
        self.assertEqual(
            UserQuest.objects.filter(user=self.user, quest=quest).count(), 1
        )

        handle_pages_read(self.user, 4)  # próximo evento relevante reinscreve

        rows = UserQuest.objects.filter(user=self.user, quest=quest)
        self.assertEqual(rows.count(), 2)
        active = rows.get(status=UserQuest.Status.IN_PROGRESS)
        # A nova rodada conta só o delta desde a reinscrição.
        self.assertEqual(active.progress_value, 4)

        # A rodada nova (4 < 10) NÃO completou: o ledger segue com o XP
        # de uma única conclusão.
        self.assertEqual(
            XPTransaction.objects.filter(
                reason=XPTransaction.Reason.QUEST_COMPLETED
            ).count(),
            1,
        )


class LinkedAchievementTests(QuestServicesTestCase):
    def test_completing_quest_unlocks_achievement_with_independent_xp(self):
        quest = make_quest("q-pages", "pages_read", 50, xp_reward=40)
        achievement = Achievement.objects.create(
            code="a-linked",
            name="a-linked",
            criteria_type="pages_read",
            source_quest=quest,
            xp_reward=25,
        )

        handle_pages_read(self.user, 60)

        user_achievement = UserAchievement.objects.get(
            user=self.user, achievement=achievement
        )

        # DOIS créditos independentes (R6): um da quest, um do achievement.
        quest_txn = XPTransaction.objects.get(reason=XPTransaction.Reason.QUEST_COMPLETED)
        ach_txn = XPTransaction.objects.get(reason=XPTransaction.Reason.ACHIEVEMENT_UNLOCKED)
        self.assertEqual(quest_txn.amount, 40)
        self.assertEqual(ach_txn.amount, 25)
        self.assertEqual(ach_txn.source_type, "user_achievement")
        self.assertEqual(ach_txn.source_id, user_achievement.id)
        self.user.refresh_from_db()
        self.assertEqual(self.user.total_xp, 65)

        self.assertTrue(
            TimelineEvent.objects.filter(
                type=TimelineEvent.Type.ACHIEVEMENT_UNLOCKED
            ).exists()
        )


class StandaloneAchievementTests(QuestServicesTestCase):
    def setUp(self):
        super().setUp()
        self.achievement = Achievement.objects.create(
            code="a-100-pages",
            name="a-100-pages",
            criteria_type="pages_read",
            criteria_value=100,
            xp_reward=10,
        )
        # Sessões reais para a métrica lifetime (Sum de end_page - start_page).
        self.book = Book.objects.create(title="Livro", total_pages=500)
        self.user_book = UserBook.objects.create(
            user=self.user, book=self.book, status=UserBook.Status.LENDO,
        )

    def _add_session(self, start_page, end_page):
        ReadingSession.objects.create(
            user_book=self.user_book,
            user=self.user,
            start_page=start_page,
            end_page=end_page,
            occurred_on=date(2026, 7, 1),
        )

    def test_below_lifetime_threshold_does_not_unlock(self):
        self._add_session(0, 60)

        handle_pages_read(self.user, 60)

        self.assertEqual(UserAchievement.objects.count(), 0)

    def test_reaching_lifetime_threshold_unlocks_once(self):
        self._add_session(0, 60)
        handle_pages_read(self.user, 60)   # lifetime 60 < 100
        self._add_session(60, 120)
        handle_pages_read(self.user, 60)   # lifetime 120 >= 100

        self.assertEqual(
            UserAchievement.objects.filter(
                user=self.user, achievement=self.achievement
            ).count(),
            1,
        )
        txn = XPTransaction.objects.get(
            reason=XPTransaction.Reason.ACHIEVEMENT_UNLOCKED
        )
        self.assertEqual(txn.amount, 10)

    def test_already_unlocked_is_not_duplicated_on_next_event(self):
        self._add_session(0, 120)
        handle_pages_read(self.user, 120)  # desbloqueia
        self._add_session(120, 150)
        handle_pages_read(self.user, 30)   # métrica continua >= 100

        self.assertEqual(UserAchievement.objects.count(), 1)
        self.assertEqual(
            XPTransaction.objects.filter(
                reason=XPTransaction.Reason.ACHIEVEMENT_UNLOCKED
            ).count(),
            1,
        )
        self.assertEqual(
            TimelineEvent.objects.filter(
                type=TimelineEvent.Type.ACHIEVEMENT_UNLOCKED
            ).count(),
            1,
        )

    def test_books_finished_lifetime_uses_lido_count(self):
        achievement = Achievement.objects.create(
            code="a-2-books",
            name="a-2-books",
            criteria_type="books_finished",
            criteria_value=2,
            xp_reward=0,
        )
        # 2 livros já LIDOs no histórico do usuário.
        for i in range(2):
            book = Book.objects.create(title=f"Lido {i}", total_pages=100)
            UserBook.objects.create(
                user=self.user, book=book, status=UserBook.Status.LIDO,
            )

        handle_book_finished(self.user)

        self.assertTrue(
            UserAchievement.objects.filter(
                user=self.user, achievement=achievement
            ).exists()
        )

    def test_streak_days_lifetime_uses_longest_count(self):
        achievement = Achievement.objects.create(
            code="a-7-streak",
            name="a-7-streak",
            criteria_type="streak_days",
            criteria_value=7,
            xp_reward=0,
        )
        Streak.objects.create(
            user=self.user, current_count=2, longest_count=9,
            last_active_on=date(2026, 7, 1),
        )

        handle_streak(self.user, 2)  # current baixo; longest (9) >= 7

        self.assertTrue(
            UserAchievement.objects.filter(
                user=self.user, achievement=achievement
            ).exists()
        )
