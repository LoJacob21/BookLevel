"""Clients HTTP das fontes externas de catálogo (Open Library, Google Books).

Camada burra de rede: cada função fala o dialeto de UMA API e traduz o
payload para o dict interno comum (documentado em search_open_library).
Nenhuma regra de negócio aqui — seleção/normalização de ISBN, dedup e
persistência vivem em catalog.services; a orquestração de fallback entre
fontes vive na task.

Erros de rede/HTTP propagam como requests.RequestException — quem decide
o que fazer com a falha é a task (R7).
"""

import requests

from catalog.models import Book

# R9: toda chamada externa se identifica.
USER_AGENT = "BookLevel/1.0 (+https://github.com/LoJacob21/BookLevel)"
TIMEOUT_SECONDS = 10

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"


def _get_json(url: str, params: dict) -> dict:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def search_open_library(query: str) -> dict | None:
    """Busca na Open Library e devolve o resultado mais relevante, ou None.

    Formato interno comum (o mesmo nas duas fontes):
      title, subtitle, total_pages (int | None), cover_url,
      published_year (int | None), isbns (lista crua de candidatos),
      source, external_id (str | None), authors (lista de nomes).
    """
    payload = _get_json(
        OPEN_LIBRARY_SEARCH_URL,
        {
            "q": query,
            "limit": 1,
            "fields": "key,title,subtitle,author_name,first_publish_year,"
            "number_of_pages_median,isbn,cover_i",
        },
    )
    docs = payload.get("docs") or []
    if not docs or not docs[0].get("title"):
        return None
    doc = docs[0]
    cover_id = doc.get("cover_i")
    return {
        "title": doc["title"],
        "subtitle": doc.get("subtitle") or "",
        "total_pages": doc.get("number_of_pages_median"),
        "cover_url": (
            f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else ""
        ),
        "published_year": doc.get("first_publish_year"),
        "isbns": doc.get("isbn") or [],
        "source": Book.Source.OPEN_LIBRARY,
        "external_id": doc.get("key"),  # ex.: "/works/OL45883W"
        "authors": doc.get("author_name") or [],
    }


def search_google_books(query: str) -> dict | None:
    """Busca no Google Books e devolve o resultado mais relevante, ou None.

    Mesmo formato interno de search_open_library.
    """
    payload = _get_json(GOOGLE_BOOKS_SEARCH_URL, {"q": query, "maxResults": 1})
    items = payload.get("items") or []
    if not items:
        return None
    item = items[0]
    info = item.get("volumeInfo") or {}
    if not info.get("title"):
        return None

    published = info.get("publishedDate") or ""  # "2005" ou "2005-03-01"
    year = int(published[:4]) if published[:4].isdigit() else None
    isbns = [
        identifier.get("identifier", "")
        for identifier in info.get("industryIdentifiers") or []
        if identifier.get("type") in ("ISBN_13", "ISBN_10")
    ]
    return {
        "title": info["title"],
        "subtitle": info.get("subtitle") or "",
        "total_pages": info.get("pageCount"),
        "cover_url": (info.get("imageLinks") or {}).get("thumbnail") or "",
        "published_year": year,
        "isbns": isbns,
        "source": Book.Source.GOOGLE_BOOKS,
        "external_id": item.get("id"),
        "authors": info.get("authors") or [],
    }
