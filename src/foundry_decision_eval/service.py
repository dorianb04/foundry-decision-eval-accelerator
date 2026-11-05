from __future__ import annotations

import secrets
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .contracts import EvaluationRequest, EvaluationResult, failure


class Scorer(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


def create_app(
    scorer: Scorer,
    *,
    api_key: str | None,
    local_only: bool = False,
    api_key_header: str = "api-key",
) -> FastAPI:
    if not api_key and not local_only:
        raise ValueError("Set EVALUATOR_API_KEY or explicitly bind an anonymous local-only service")
    app = FastAPI(title="Foundry Decision endpoint evaluator", docs_url=None, redoc_url=None)

    async def authorize(
        provided_key: str | None = Header(default=None, alias=api_key_header),
    ) -> None:
        if api_key and (provided_key is None or not secrets.compare_digest(provided_key, api_key)):
            raise HTTPException(status_code=401, detail="Invalid or missing evaluator API key")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=failure("invalid_request", "Request does not match schema 0.0.1").wire(),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ready", "schema_version": "0.0.1"}

    @app.post("/evaluate", dependencies=[Depends(authorize)])
    async def evaluate(request: EvaluationRequest) -> JSONResponse:
        result = await scorer.evaluate(request)
        return JSONResponse(content=result.wire())

    return app
