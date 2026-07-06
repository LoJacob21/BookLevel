"""Tasks Celery do catálogo (importação externa de livros).

Task magra por decisão de arquitetura: orquestra o fallback entre fontes
(R7) e o retry (R8); toda a regra de negócio (dedup, autores, persistência)
vive em catalog.services.import_book.
"""

import logging
import random

import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from catalog.external import search_google_books, search_open_library
from catalog.services import import_book

logger = logging.getLogger(__name__)


# Backoff exponencial com teto de 60s + full jitter, a mesma fórmula que o
# autoretry do Celery usa (retry_backoff/retry_jitter) — replicada aqui
# porque essas opções do decorator NÃO se aplicam a self.retry() manual.
RETRY_BACKOFF_MAX_SECONDS = 60


def _retry_countdown(retries: int) -> float:
    return random.uniform(0, min(2**retries, RETRY_BACKOFF_MAX_SECONDS))


@shared_task(
    bind=True,
    max_retries=3,  # R8
    rate_limit="10/m",  # cortesia com as fontes (R9)
    ignore_result=True,  # fire-and-forget: ninguém consome o retorno
)
def fetch_and_import_book(self, query: str) -> None:
    """Busca `query` nas fontes externas e importa o melhor resultado.

    R7: Google Books entra tanto quando a Open Library falha (erro de
    rede/HTTP/timeout) quanto quando ela responde vazio — os dois casos
    são "tentar a próxima fonte".
    """
    data = None
    last_error: requests.RequestException | None = None

    try:
        data = search_open_library(query)
    except requests.RequestException as exc:
        last_error = exc
        logger.warning("Open Library falhou para %r: %s", query, exc)

    if data is None:
        try:
            data = search_google_books(query)
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Google Books falhou para %r: %s", query, exc)

    if data is None:
        if last_error is not None:
            # Falha de rede em cadeia: vale retry (R8). Na exaustão a task
            # morre logada — sem dead-letter queue no MVP.
            try:
                raise self.retry(countdown=_retry_countdown(self.request.retries))
            except MaxRetriesExceededError:
                logger.error(
                    "importação externa esgotou retries; query=%r; último erro: %s",
                    query,
                    last_error,
                )
            return
        # Nenhuma fonte conhece o livro: fim normal, nada a importar.
        logger.info("nenhum resultado externo para %r", query)
        return

    import_book(data)
