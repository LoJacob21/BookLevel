# BookLevel — Implementação dos Django Models

> Blueprint para implementar no Claude Code + Antigravity.
> Traduz o modelo relacional aprovado em Django models, com as decisões já resolvidas.
> Alvo: Django 5.2 LTS + PostgreSQL.

---

## 0. Antes da primeira migração — decisões que NÃO podem ser adiadas

Duas coisas precisam estar certas **antes** de rodar o primeiro `migrate`, porque mudá-las depois é doloroso:

1. **Custom User model.** Definir `AUTH_USER_MODEL = "accounts.User"` no settings antes da primeira migração. Trocar o modelo de usuário depois que as migrações já rodaram é um dos piores retrabalhos do Django. Já começamos com ele.
2. **Extensão do PostgreSQL para UUID.** Usamos `default=uuid.uuid4` (gerado pelo Python), então não dependemos de extensão para isso. Mas se preferir gerar no banco (`gen_random_uuid()`), habilite `pgcrypto` numa migração inicial.

### Nota de versão sobre constraints
No Django 5.1+, `CheckConstraint` usa o parâmetro `condition=`. Em Django ≤ 5.0 (incl. 4.2 LTS) é `check=`. Os exemplos abaixo usam `condition=`; ajuste se estiver no 4.2.

---

## 1. Estrutura de apps

Um app por contexto do domínio — mantém o mapeamento domínio→código limpo:

```
booklevel/
  accounts/        # User, AvatarPreset, ApiToken (infra da API)
  catalog/         # Author, Genre, Book
  library/         # UserBook, ReadingSession, DiaryEntry, Review, Quote, FavoriteCharacter
  goals/           # Goal
  gamification/    # Level, XPTransaction, Streak, StreakFreeze
  quests/          # Quest, UserQuest, Achievement, UserAchievement
  timeline/        # TimelineEvent (+ Reaction, Comment, Follow — futuro)
  events/          # SeasonEvent, CosmeticReward, UserCosmetic — futuro
  communities/     # Community, CommunityMembership — futuro
```

`INSTALLED_APPS`: registre `accounts` antes de rodar qualquer migração.

Direção de dependência entre **services** (consolidada na Onda 2):
`library → quests → gamification → timeline`. Helpers compartilhados de
XP/timeline (`today_for`, `create_level_up_event`) vivem em
`gamification.services`; `quests` importa `library.models` apenas para
métricas lifetime, nunca `library.services` — sem ciclos.

---

## 2. Como cada construção relacional vira Django

| Relacional | Django |
|---|---|
| PK `uuid` | `models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)` |
| `CHECK (...)` | `Meta.constraints = [CheckConstraint(condition=Q(...))]` |
| `UNIQUE (a, b)` | `UniqueConstraint(fields=["a", "b"])` |
| índice único parcial `WHERE ...` | `UniqueConstraint(fields=[...], condition=Q(...))` |
| UNIQUE case-insensitive | `UniqueConstraint(Lower("campo"))` |
| enum | `models.TextChoices` + `choices=` |
| FK sem rígida (ledger) | campos `source_type`/`source_id` soltos, sem `ForeignKey` |
| índice de consulta | `Meta.indexes = [models.Index(fields=[...])]` |

---

## 3. accounts

```python
import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, nickname, password=None, **extra):
        if not email:
            raise ValueError("Email é obrigatório")
        user = self.model(email=self.normalize_email(email), nickname=nickname, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, nickname, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, nickname, password, **extra)


class AvatarPreset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    image_url = models.URLField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    # email é o login; nickname é o handle público. Removemos username/nome real.
    username = None
    first_name = None
    last_name = None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    nickname = models.CharField(max_length=40)
    bio = models.TextField(blank=True)
    timezone = models.CharField(max_length=64)  # IANA, ex.: "America/Sao_Paulo"
    avatar_preset = models.ForeignKey(
        AvatarPreset, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="users",
    )
    total_xp = models.PositiveIntegerField(default=0)            # cache do ledger
    current_level = models.PositiveSmallIntegerField(default=1)  # cache derivado
    email_verified_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nickname"]
    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("nickname"), name="uq_user_nickname_ci"),
        ]

    def __str__(self):
        return self.nickname
```

Notas: `current_level` é `PositiveSmallIntegerField` simples (não FK para `Level`), de propósito — é um cache, e mantê-lo como número evita acoplamento circular entre apps; o título do nível busca-se em `Level` quando necessário.

```python
def generate_token_key() -> str:
    # Função de módulo (não lambda) para ser serializável na migração.
    return secrets.token_hex(32)  # 64 chars hex


class ApiToken(models.Model):
    """Infraestrutura da API (Onda 3), não domínio: token opaco (bearer).

    Um user pode ter vários (um por device). Logout deleta o token da
    request; exclusão de conta deleta todos.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    key = models.CharField(max_length=64, unique=True, default=generate_token_key, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
```

---

## 4. catalog

```python
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
```

Notas: na importação da API externa, antes de criar o `Book`, resolver autores/gêneros por `normalized_name` (reaproveitar existentes) — é o que evita duplicação. Isso vive numa camada de serviço, não no model.

---

## 5. library

```python
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
```

Usamos `OneToOneField` em `Review.user_book` — é a forma idiomática do Django para "no máximo uma resenha por leitura" (equivale ao UNIQUE da modelagem).

---

## 6. goals

```python
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class Goal(models.Model):
    class Metric(models.TextChoices):
        PAGES = "pages", "Páginas"
        BOOKS = "books", "Livros"
        MINUTES = "minutes", "Minutos"

    class Period(models.TextChoices):
        DAILY = "daily", "Diária"
        WEEKLY = "weekly", "Semanal"
        MONTHLY = "monthly", "Mensal"
        YEARLY = "yearly", "Anual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="goals")
    user_book = models.ForeignKey("library.UserBook", null=True, blank=True, on_delete=models.CASCADE, related_name="goals")
    metric = models.CharField(max_length=10, choices=Metric.choices)
    period = models.CharField(max_length=10, choices=Period.choices)
    target_value = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(target_value__gt=0), name="ck_goal_target_pos"),
            # 1 meta global ativa por user+metric+period
            models.UniqueConstraint(
                fields=["user", "metric", "period"],
                condition=Q(user_book__isnull=True, is_active=True),
                name="uq_goal_global_active",
            ),
            # 1 meta por livro ativa por user_book+metric+period
            models.UniqueConstraint(
                fields=["user_book", "metric", "period"],
                condition=Q(user_book__isnull=False, is_active=True),
                name="uq_goal_book_active",
            ),
        ]
```

A precedência (meta do livro sobrescreve a global) é resolvida na consulta da camada de serviço: procura meta do `user_book`; se não houver, cai para a global.

---

## 7. gamification

```python
import uuid
from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Level(models.Model):
    level_number = models.PositiveSmallIntegerField(primary_key=True)
    xp_required = models.PositiveIntegerField()  # XP acumulado para atingir este nível
    title = models.CharField(max_length=80)      # "Leitor Iniciante", ...

    class Meta:
        ordering = ["level_number"]
    # linhas geradas por fórmula no seed; editáveis via Admin


class XPTransaction(models.Model):
    class Reason(models.TextChoices):
        PAGES_READ = "pages_read", "Páginas lidas"
        BOOK_COMPLETED = "book_completed", "Livro concluído"
        REVIEW_WRITTEN = "review_written", "Resenha escrita"
        GOAL_MET = "goal_met", "Meta cumprida"
        QUEST_COMPLETED = "quest_completed", "Quest concluída"
        ACHIEVEMENT_UNLOCKED = "achievement_unlocked", "Conquista desbloqueada"
        CORRECTION = "correction", "Correção"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="xp_transactions")
    amount = models.IntegerField()  # pode ser negativo (correção)
    reason = models.CharField(max_length=24, choices=Reason.choices)
    source_type = models.CharField(max_length=40, blank=True)  # sem FK rígida (ledger)
    source_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"], name="ix_xp_user_created")]

    # Imutabilidade: bloqueia UPDATE e DELETE no nível do model.
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("XPTransaction é imutável — crie uma transação de correção.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("XPTransaction não pode ser apagada.")


class Streak(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="streak")
    current_count = models.PositiveIntegerField(default=0)
    longest_count = models.PositiveIntegerField(default=0)
    last_active_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(longest_count__gte=F("current_count")), name="ck_streak_longest"),
        ]


class StreakFreeze(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="streak_freezes")
    used_on = models.DateField()
    period_key = models.CharField(max_length=7)  # "2026-06"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "period_key"], name="uq_freeze_user_period"),
        ]
```

A imutabilidade aqui é reforçada no model. Para garantia absoluta (mesmo contra `update()` em queryset ou SQL bruto), o ideal é complementar com um **trigger no banco** via migração `RunSQL` que rejeita UPDATE/DELETE — fica como reforço recomendado.

O **cache de `total_xp`** NÃO é atualizado no model. Vive numa camada de serviço, dentro de `transaction.atomic()`:

```python
# gamification/services.py (esboço)
from django.db import transaction
from django.db.models import F

@transaction.atomic
def grant_xp(user, amount, reason, source_type="", source_id=None):
    XPTransaction.objects.create(
        user=user, amount=amount, reason=reason,
        source_type=source_type, source_id=source_id,
    )
    # atualiza o cache na MESMA transação
    User.objects.filter(pk=user.pk).update(total_xp=F("total_xp") + amount)
    # recalcular current_level a partir da tabela Level
    ...
```

Uma task Celery periódica reconcilia `total_xp` com `SUM(amount)` do ledger.

---

## 8. quests

```python
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class Quest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    criteria_type = models.CharField(max_length=40)
    criteria_value = models.PositiveIntegerField()
    is_repeatable = models.BooleanField(default=False)
    event_id = models.UUIDField(null=True, blank=True)      # escopo opcional (events.SeasonEvent)
    community_id = models.UUIDField(null=True, blank=True)   # escopo opcional (communities.Community)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    xp_reward = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(criteria_value__gt=0), name="ck_quest_criteria_pos"),
            models.CheckConstraint(condition=Q(xp_reward__gte=0), name="ck_quest_xp_reward_nonneg"),
        ]


class UserQuest(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "Em andamento"
        COMPLETED = "completed", "Concluída"
        EXPIRED = "expired", "Expirada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_quests")
    quest = models.ForeignKey(Quest, on_delete=models.CASCADE, related_name="user_quests")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)
    progress_value = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # no máximo 1 quest ativa por user+quest
            models.UniqueConstraint(
                fields=["user", "quest"],
                condition=Q(status="in_progress"),
                name="uq_userquest_active",
            ),
        ]
        indexes = [models.Index(fields=["user", "status"], name="ix_userquest_user_status")]


class Achievement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    criteria_type = models.CharField(max_length=40)
    criteria_value = models.PositiveIntegerField(null=True, blank=True)
    source_quest = models.ForeignKey(Quest, null=True, blank=True, on_delete=models.SET_NULL, related_name="achievements")
    event_id = models.UUIDField(null=True, blank=True)
    xp_reward = models.PositiveIntegerField(default=0)


class UserAchievement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_achievements")
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE, related_name="user_achievements")
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "achievement"], name="uq_userachievement"),
        ]
```

`event_id`/`community_id` ficam como `UUIDField` solto (não FK) enquanto `events`/`communities` são futuro — vira `ForeignKey` quando esses apps existirem, numa migração simples.

---

## 9. timeline

```python
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class TimelineEvent(models.Model):
    class Type(models.TextChoices):
        BOOK_STARTED = "book_started", "Livro iniciado"
        BOOK_FINISHED = "book_finished", "Livro concluído"
        LEVEL_UP = "level_up", "Subiu de nível"
        ACHIEVEMENT_UNLOCKED = "achievement_unlocked", "Conquista desbloqueada"
        STREAK_KEPT = "streak_kept", "Ofensiva mantida"
        QUEST_COMPLETED = "quest_completed", "Quest concluída"
        DAILY_SUMMARY = "daily_summary", "Resumo do dia"

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Privado"
        FOLLOWERS = "followers", "Seguidores"
        PUBLIC = "public", "Público"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timeline_events")
    type = models.CharField(max_length=24, choices=Type.choices)
    payload = models.JSONField(default=dict)
    event_date = models.DateField()
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PRIVATE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # 1 resumo diário por usuário por dia
            models.UniqueConstraint(
                fields=["user", "event_date"],
                condition=Q(type="daily_summary"),
                name="uq_timeline_daily_summary",
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="ix_timeline_user_created")]

# Reaction, Comment, Follow — futuro (pós-MVP). Seguem o mesmo padrão;
# Follow tem UniqueConstraint(follower, following) + CheckConstraint(~Q(follower=F("following"))).
```

---

## 10. events / communities — futuro

Modelados no relacional, implementados nas fases futuras. Quando entrarem:
- `events.SeasonEvent`, `CosmeticReward`, `UserCosmetic`.
- `communities.Community`, `CommunityMembership`.
- Os campos `event_id`/`community_id` em `Quest`/`Achievement` viram `ForeignKey` de verdade.

---

## 11. Ordem de implementação sugerida (incremental)

Segue as ondas do roadmap — cada bloco é migrável e testável isoladamente:

1. `accounts` (User custom + AvatarPreset) → primeira migração, com `AUTH_USER_MODEL` setado.
2. `catalog` (Author, Genre, Book).
3. `library` (UserBook, ReadingSession, DiaryEntry, Review).
4. `goals` (Goal — só daily no MVP).
5. `gamification` (Level + seed por fórmula, XPTransaction, services de XP).
6. `timeline` (TimelineEvent).
7. **Onda 2:** `quests` + Streak/StreakFreeze + Quote/FavoriteCharacter.
8. **Pós-MVP:** social (Reaction/Comment/Follow), `events`, `communities`.

Depois dos models de cada bloco: `makemigrations` → revisar a migração gerada → `migrate`.
```
