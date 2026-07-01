"""Seed idempotente da tabela Level (níveis 1–50).

Level é editável via Admin depois; por isso o seed é um management command
rerodável (update_or_create), não uma data migration. Os tiers ficam como dados
no próprio comando para facilitar estender no futuro (ex.: até o nível 99).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from gamification.models import Level

# (nível_inicial, nível_final, nome_do_tier)
TIERS = [
    (1, 4, "Leitor Iniciante"),
    (5, 9, "Aventureiro"),
    (10, 14, "Bibliotecário"),
    (15, 19, "Guardião de Histórias"),
    (20, 24, "Mestre dos Livros"),
    (25, 29, "Sábio das Páginas"),
    (30, 34, "Andarilho das Letras"),
    (35, 39, "Cronista das Eras"),
    (40, 44, "Lenda Literária"),
    (45, 49, "Arquimago das Histórias"),
    (50, 50, "Imortal das Páginas"),
]


def int_to_roman(value: int) -> str:
    numerals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    result = []
    for n, symbol in numerals:
        while value >= n:
            result.append(symbol)
            value -= n
    return "".join(result)


def title_for(tier_name: str, position: int) -> str:
    """Título do nível dentro do tier.

    O primeiro nível do tier (position=0) usa o nome puro; os seguintes recebem
    numeral romano a partir do II. Tier de nível único só tem position=0, logo
    nunca recebe numeral.
    """
    if position == 0:
        return tier_name
    return f"{tier_name} {int_to_roman(position + 1)}"


def xp_required(level_number: int) -> int:
    """XP acumulado para atingir o nível. Nível 1 fixado em 0."""
    if level_number == 1:
        return 0
    return round(50 * level_number ** 1.8)


class Command(BaseCommand):
    help = "Popula/atualiza a tabela Level (níveis 1–50) de forma idempotente."

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0
        for start, end, name in TIERS:
            for level_number in range(start, end + 1):
                _, was_created = Level.objects.update_or_create(
                    level_number=level_number,
                    defaults={
                        "xp_required": xp_required(level_number),
                        "title": title_for(name, level_number - start),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Levels seedados: {created} criados, {updated} atualizados "
            f"(total {created + updated})."
        ))
