import uuid

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class Author(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("normalized_name"), name="uq_author_norm")]

    def __str__(self):
        return self.name


class Genre(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=120)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("normalized_name"), name="uq_genre_norm")]

    def __str__(self):
        return self.name


class Book(models.Model):
    class Source(models.TextChoices):
        GOOGLE_BOOKS = "google_books", "Google Books"
        OPEN_LIBRARY = "open_library", "Open Library"
        MANUAL = "manual", "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    title = models.CharField(max_length=500)
    subtitle = models.CharField(max_length=500, blank=True)
    total_pages = models.PositiveIntegerField()
    cover_url = models.URLField(blank=True)
    published_year = models.SmallIntegerField(null=True, blank=True)
    authors = models.ManyToManyField(Author, related_name="books")
    genres = models.ManyToManyField(Genre, related_name="books")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(total_pages__gt=0), name="ck_book_pages_pos"),
        ]

    def __str__(self):
        return self.title
