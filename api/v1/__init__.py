"""API v1 do BookLevel (Django Ninja).

Princípio inegociável: endpoints são cascas finas — validam entrada
(Schema), chamam os services e serializam saída. Nenhuma regra de
negócio nesta camada.
"""

from django.core.exceptions import ValidationError
from ninja import NinjaAPI

from api.auth import TokenAuth

from .accounts import router as accounts_router
from .catalog import router as catalog_router
from .library import router as library_router

api = NinjaAPI(
    title="BookLevel API",
    version="1",
    urls_namespace="api-v1",
    # Autenticado por padrão (seguro por omissão); endpoints públicos
    # (register/login) optam por sair com auth=None.
    auth=TokenAuth(),
)


@api.exception_handler(ValidationError)
def validation_error_to_400(request, exc: ValidationError):
    # ValidationError dos services -> HTTP 400 {"detail": msg}.
    return api.create_response(
        request, {"detail": "; ".join(exc.messages)}, status=400
    )


api.add_router("/auth", accounts_router)
api.add_router("/books", catalog_router)
api.add_router("/library", library_router)
