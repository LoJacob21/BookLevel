"""Autenticação por token (bearer) da API."""

from django.utils import timezone
from ninja.security import HttpBearer

from accounts.models import ApiToken


class TokenAuth(HttpBearer):
    """Resolve `Authorization: Bearer <key>` para o User dono do token.

    Retornar None faz o Ninja responder 401 — tanto para token inexistente
    quanto para user desativado (soft-delete revoga o acesso na hora).
    O ApiToken usado fica em request.api_token para endpoints que precisam
    dele (ex.: logout deleta exatamente o token da request).
    """

    def authenticate(self, request, token: str):
        api_token = (
            ApiToken.objects.select_related("user").filter(key=token).first()
        )
        if api_token is None or not api_token.user.is_active:
            return None

        # update() direto: não dispara save() de instância nem toca em nada
        # além do last_used_at.
        ApiToken.objects.filter(pk=api_token.pk).update(last_used_at=timezone.now())
        request.api_token = api_token
        return api_token.user
