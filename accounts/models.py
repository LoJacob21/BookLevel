import uuid

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    # Necessário porque USERNAME_FIELD = "email" (o manager padrão espera username).
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
