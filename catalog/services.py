"""Regras de negócio do catálogo: importação externa com deduplicação.

R4: dedup em cascata — ISBN-13 normalizado -> ISBN-10 convertido para 13
-> (source, external_id). R5: Author por matching case-insensitive na
chave normalized_name (constraint uq_author_norm); Genre não é importado
no MVP (subjects das fontes são ruidosos — curadoria manual no Admin).
R3: livro já importado não é re-enriquecido.
"""

from django.db import IntegrityError, transaction

from catalog.models import Author, Book


def normalize_author_name(name: str) -> str:
    # Chave canônica de dedup do Author: trim + espaços internos colapsados.
    # Caixa fica por conta do Lower() da uq_author_norm / lookup __iexact.
    return " ".join(name.split())


def isbn10_to_isbn13(isbn10: str) -> str:
    """Converte ISBN-10 (já sem hífens/espaços) para ISBN-13.

    O dígito verificador do ISBN-10 é descartado (não vale para o 13);
    o do ISBN-13 é recalculado sobre "978" + os 9 dígitos de corpo.
    """
    core = "978" + isbn10[:9]
    total = sum(
        int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(core)
    )
    return core + str((10 - total % 10) % 10)


def select_isbn13(candidates: list[str]) -> str | None:
    """Escolhe o ISBN canônico entre os candidatos crus da fonte.

    Preferência do R4: um ISBN-13 direto; só na ausência dele, o primeiro
    ISBN-10 válido convertido. Retorna sempre 13 dígitos, ou None — nunca
    string vazia.
    """
    isbn10_fallback = None
    for raw in candidates:
        digits = raw.replace("-", "").replace(" ", "").upper()
        if len(digits) == 13 and digits.isdigit():
            return digits
        if (
            isbn10_fallback is None
            and len(digits) == 10
            and digits[:9].isdigit()
            and (digits[9].isdigit() or digits[9] == "X")
        ):
            isbn10_fallback = digits
    if isbn10_fallback is not None:
        return isbn10_to_isbn13(isbn10_fallback)
    return None


def import_book(data: dict) -> Book:
    """Persiste um resultado externo (formato de catalog.external) com dedup.

    Se a cascata do R4 encontra match, devolve o Book existente intocado
    (R3: sem re-enriquecimento no MVP); senão cria o Book completo e
    resolve os autores.
    """
    isbn = select_isbn13(data.get("isbns") or [])
    # Ausência é SEMPRE None, nunca "": as uniques parciais condicionam em
    # isnull=False, então "" entraria na constraint e faria dois livros sem
    # ISBN (ou sem id externo) colidirem entre si.
    external_id = data.get("external_id") or None
    source = data["source"]

    existing = _find_existing(isbn, source, external_id)
    if existing is not None:
        return existing

    try:
        # atomic DENTRO do try: o savepoint isola o IntegrityError da
        # corrida entre tasks importando o mesmo livro em paralelo.
        with transaction.atomic():
            book = Book.objects.create(
                title=data["title"],
                subtitle=data.get("subtitle") or "",
                # R6: fonte sem contagem de páginas importa com 0; o service
                # de sessão já bloqueia leitura até a curadoria preencher.
                total_pages=data.get("total_pages") or 0,
                cover_url=data.get("cover_url") or "",
                published_year=data.get("published_year"),
                isbn=isbn,
                source=source,
                external_id=external_id,
            )
            for author_name in data.get("authors") or []:
                book.authors.add(_resolve_author(author_name))
    except IntegrityError:
        # Outro worker venceu a corrida entre o lookup e o create — a
        # cascata agora encontra o livro dele.
        existing = _find_existing(isbn, source, external_id)
        if existing is not None:
            return existing
        raise
    return book


def _find_existing(isbn: str | None, source: str, external_id: str | None) -> Book | None:
    # Cascata R4, na ordem: ISBN-13 primeiro (cruza fontes diferentes do
    # mesmo livro), (source, external_id) só como último recurso.
    if isbn:
        book = Book.objects.filter(isbn=isbn).first()
        if book is not None:
            return book
    if external_id:
        return Book.objects.filter(source=source, external_id=external_id).first()
    return None


def _resolve_author(name: str) -> Author:
    normalized = normalize_author_name(name)
    author = Author.objects.filter(normalized_name__iexact=normalized).first()
    if author is not None:
        return author
    try:
        # Mesmo padrão de savepoint: corrida com outra task criando o mesmo
        # autor morre na uq_author_norm e cai no get abaixo.
        with transaction.atomic():
            return Author.objects.create(name=name.strip(), normalized_name=normalized)
    except IntegrityError:
        return Author.objects.get(normalized_name__iexact=normalized)
