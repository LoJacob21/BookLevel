"""Serviços de contas.

deactivate_account() é a rotina de "exclusão" de conta do MVP (R10):
user.delete() real nunca funciona para quem tem histórico de XP —
gamification.XPTransaction.user usa PROTECT e há trigger de imutabilidade
no banco (por design: o ledger não perde linhas). A saída é soft-delete:
desativar o login e anonimizar os dados pessoais, preservando todo o
histórico de leitura/XP/timeline.
"""

import uuid

from django.db import transaction

from .models import User


@transaction.atomic
def deactivate_account(user: User) -> User:
    """Desativa e anonimiza a conta do usuário (soft-delete, R10).

    Na MESMA transação: is_active=False, email e nickname trocados por
    placeholders únicos (o sufixo aleatório evita colisão com os índices
    únicos de email e uq_user_nickname_ci) e bio limpa. NADA além desses
    quatro campos é tocado — dados de leitura, XP e timeline permanecem.

    O domínio .invalid é reservado (RFC 2606): o email anonimizado nunca
    é entregável nem colide com um endereço real.
    """
    token = uuid.uuid4()

    user.is_active = False
    user.email = f"deleted-{token}@anonymized.invalid"
    # nickname tem max_length=40: "deleted-" + 12 hex cabem com folga.
    user.nickname = f"deleted-{token.hex[:12]}"
    user.bio = ""
    user.save(update_fields=["is_active", "email", "nickname", "bio"])

    return user
