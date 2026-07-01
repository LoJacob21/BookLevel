import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class UserBook(models.Model):
    class Status(models.TextChoices):
        QUERO_LER = "quero_ler", "Quero Ler"
        LENDO = "lendo", "Lendo"
        LIDO = "lido", "Lido"
        ABANDONADO = "abandonado", "Abandonado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_books")
    book = models.ForeignKey("catalog.Book", on_delete=models.PROTECT, related_name="user_books")
    status = models.CharField(max_length=12, choices=Status.choices)
    current_page = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "book"], name="uq_userbook_user_book"),
        ]
        indexes = [models.Index(fields=["user", "status"], name="ix_userbook_user_status")]

    # current_page <= book.total_pages: validado na aplicação (clean()/serializer)


class ReadingSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_book = models.ForeignKey(UserBook, on_delete=models.CASCADE, related_name="sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reading_sessions")  # denormalizado
    start_page = models.PositiveIntegerField()
    end_page = models.PositiveIntegerField()
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    occurred_on = models.DateField()  # dia no fuso do usuário
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(end_page__gte=F("start_page")), name="ck_session_pages"),
        ]
        indexes = [
            models.Index(fields=["user", "occurred_on"], name="ix_session_user_day"),
            models.Index(fields=["user_book"], name="ix_session_userbook"),
        ]


class DiaryEntry(models.Model):
    class EntryType(models.TextChoices):
        NOTE = "note", "Nota"
        THEORY = "theory", "Teoria"
        EMOTION = "emotion", "Emoção"
        MILESTONE = "milestone", "Marco"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_book = models.ForeignKey(UserBook, on_delete=models.CASCADE, related_name="diary_entries")
    entry_type = models.CharField(max_length=12, choices=EntryType.choices, default=EntryType.NOTE)
    mood_tag = models.CharField(max_length=40, blank=True)
    chapter_label = models.CharField(max_length=120, blank=True)
    page_at_entry = models.PositiveIntegerField(null=True, blank=True)
    body = models.TextField()
    is_spoiler = models.BooleanField(default=False)
    entry_date = models.DateField()

    class Meta:
        indexes = [models.Index(fields=["user_book", "entry_date"], name="ix_diary_userbook_date")]


class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_book = models.OneToOneField(UserBook, on_delete=models.CASCADE, related_name="review")
    rating = models.PositiveSmallIntegerField()
    body = models.TextField(blank=True)
    is_spoiler = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(rating__gte=1) & Q(rating__lte=5), name="ck_review_rating"),
        ]
    # regra "só quando status=lido": validada na aplicação


class Quote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_book = models.ForeignKey(UserBook, on_delete=models.CASCADE, related_name="quotes")
    text = models.TextField()
    page = models.PositiveIntegerField(null=True, blank=True)
    is_spoiler = models.BooleanField(default=False)


class FavoriteCharacter(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_book = models.ForeignKey(UserBook, on_delete=models.CASCADE, related_name="favorite_characters")
    name = models.CharField(max_length=120)
    note = models.TextField(blank=True)
