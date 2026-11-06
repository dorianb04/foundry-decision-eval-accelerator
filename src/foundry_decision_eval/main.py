import json
import logging
import os
import secrets
from contextlib import asynccontextmanager

import httpx
import uvicorn
from azure.core.exceptions import ClientAuthenticationError
from azure.identity.aio import AzureCliCredential, DefaultAzureCredential
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .contracts import EvaluationRequest, Rubric, error_result
from .decision import DecisionClient
from .evaluators import load_rubrics

logger = logging.getLogger(__name__)


def create_app(
    *,
    rubrics: list[Rubric] | None = None,
    decision: DecisionClient | None = None,
    api_key: str | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configured = rubrics
        if configured is None:
            configured = (
                [
                    Rubric.model_validate(item)
                    for item in json.loads(os.environ["EVALUATORS_CONFIG_JSON"])
                ]
                if os.environ.get("EVALUATORS_CONFIG_JSON")
                else load_rubrics(os.environ.get("EVALUATORS_CONFIG", "examples/rubrics.json"))
            )
        app.state.rubrics = {(rubric.name, rubric.version): rubric for rubric in configured}
        if not configured or len(app.state.rubrics) != len(configured):
            raise ValueError("Configure unique evaluator name/version pairs")
        app.state.api_key = api_key or os.environ["EVALUATOR_API_KEY"]
        if not app.state.api_key:
            raise ValueError("An evaluator API key is required")
        if decision is not None:
            app.state.decision = decision
            yield
        else:
            credential_type = (
                AzureCliCredential
                if os.environ.get("AZURE_CREDENTIAL_MODE") == "cli"
                else DefaultAzureCredential
            )
            async with (
                credential_type(process_timeout=5) as credential,
                httpx.AsyncClient() as http,
            ):
                app.state.decision = DecisionClient(
                    os.environ["AZURE_AI_RESOURCE_ENDPOINT"],
                    os.environ.get("DECISION_DEPLOYMENT", "Decision-1"),
                    credential,
                    http,
                )
                yield

    app = FastAPI(title="Foundry Decision evaluator", lifespan=lifespan, docs_url=None)

    def respond(result):
        if os.environ.get("FOUNDRY_PREVIEW_COMPAT") != "1":
            return result
        if result["status"] == "error":
            return JSONResponse(status_code=502, content=result)
        return {**result, "status": "Completed", "reason": result.get("reason") or ""}

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        logger.warning(
            "Invalid evaluator request fields: %s",
            [(error["loc"], error["type"]) for error in _exc.errors()],
        )
        return JSONResponse(
            status_code=422, content=error_result("invalid_request", "Expected schema 0.0.1")
        )

    @app.get("/health")
    async def health():
        return {"status": "ready"}

    @app.post("/evaluate")
    async def evaluate(
        request: EvaluationRequest,
        key: str | None = Header(
            default=None,
            alias=os.environ.get("EVALUATOR_API_KEY_HEADER", "api-key"),
        ),
    ):
        if key is None or not secrets.compare_digest(key, app.state.api_key):
            raise HTTPException(status_code=401, detail="Invalid or missing evaluator API key")
        rubric = app.state.rubrics.get((request.evaluator_name, request.evaluator_version))
        if rubric is None:
            return respond(
                error_result("unknown_evaluator", "Evaluator name/version is not configured")
            )
        try:
            return respond(
                await app.state.decision.evaluate(rubric, request.data.item, request.data.sample)
            )
        except (TimeoutError, httpx.TimeoutException):
            return respond(
                error_result("timeout", "Decision exceeded the bounded inference budget")
            )
        except httpx.HTTPStatusError as exc:
            return respond(
                error_result(
                    "model_http_error", f"Decision returned HTTP {exc.response.status_code}"
                )
            )
        except httpx.RequestError:
            return respond(error_result("connection_error", "Could not connect to Decision"))
        except ClientAuthenticationError:
            return respond(
                error_result("authentication_error", "Could not acquire an Azure inference token")
            )
        except ValueError as exc:
            logger.warning("Evaluation rejected: %s", exc)
            return respond(error_result("invalid_evaluation", str(exc)))
        except (KeyError, TypeError, IndexError):
            return respond(
                error_result(
                    "invalid_evaluation", "Missing mapped fields or invalid model response"
                )
            )

    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), access_log=False)


if __name__ == "__main__":
    main()
