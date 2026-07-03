"""Endpoints de conta e autenticação (montados sob /auth).

Cascas finas: validação de entrada nos Schemas, regra de negócio nos
services (deactivate_account) ou no UserManager (create_user).
"""

import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import field_validator

from accounts.models import ApiToken
from accounts.services import deactivate_account

User = get_user_model()

router = Router(tags=["auth"])


class RegisterIn(Schema):
    email: str
    nickname: str
    password: str
    # Opcional, mas importante: o MVP não tem update de perfil, então este é
    # o único ponto onde o fuso entra — e today_for() (streak, event_date)
    # depende dele. "" mantém o fallback para o fuso do projeto.
    timezone: str = ""

    @field_validator("email")
    @classmethod
    def _email_valido(cls, value: str) -> str:
        # Reusa o validador do Django; ValueError é o que o pydantic espera.
        try:
            validate_email(value)
        except DjangoValidationError as exc:
            raise ValueError("email inválido") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_valida(cls, value: str) -> str:
        if value == "":
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone IANA inválida") from exc
        return value


class UserOut(Schema):
    id: uuid.UUID
    email: str
    nickname: str


class LoginIn(Schema):
    email: str
    password: str


class TokenOut(Schema):
    token: str


@router.post("/register", response={201: UserOut}, auth=None)
def register(request, payload: RegisterIn):
    # Pré-checagens para mensagens claras (case-insensitive, como os índices
    # únicos do banco); o except IntegrityError cobre a janela de corrida.
    if User.objects.filter(email__iexact=payload.email).exists():
        raise HttpError(400, "Já existe uma conta com este email.")
    if User.objects.filter(nickname__iexact=payload.nickname).exists():
        raise HttpError(400, "Este nickname já está em uso.")
    try:
        user = User.objects.create_user(
            email=payload.email,
            nickname=payload.nickname,
            password=payload.password,
            timezone=payload.timezone,
        )
    except IntegrityError:
        raise HttpError(400, "Email ou nickname já está em uso.")
    return 201, user


@router.post("/login", response=TokenOut, auth=None)
def login(request, payload: LoginIn):
    # ModelBackend resolve o kwarg `email` via USERNAME_FIELD e já recusa
    # user inativo (authenticate -> None).
    user = authenticate(request, email=payload.email, password=payload.password)
    if user is None:
        raise HttpError(401, "Credenciais inválidas.")
    api_token = ApiToken.objects.create(user=user)
    return {"token": api_token.key}


@router.post("/logout", response={204: None})
def logout(request):
    # TokenAuth deixou o token desta request em request.api_token —
    # logout revoga só ele (outros devices continuam logados).
    request.api_token.delete()
    return 204, None


@router.delete("/account", response={204: None})
def delete_account(request):
    user = request.auth
    # Mesma transação: anonimização + revogação de TODOS os tokens.
    with transaction.atomic():
        deactivate_account(user)
        user.api_tokens.all().delete()
    return 204, None
