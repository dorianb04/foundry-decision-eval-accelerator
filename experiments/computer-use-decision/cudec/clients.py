"""Model clients. Tokens come from the Azure CLI at run time only and are never logged.

The Decision-1 client subclasses the accelerator's tested DecisionClient (endpoint
validation, URL building, token scope) and only adds a multi-question `ask`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any

import httpx
from azure.core.credentials import AccessToken
from foundry_decision_eval.decision import TOKEN_SCOPE, DecisionClient

from .budget import Budget

RESOURCE = "https://azfdylgeqr7cocrofq.services.ai.azure.com"
DECISION_DEPLOYMENT = "Decision-1"


class AzCliCredential:
    """Minimal async credential: `az account get-access-token`, cached until near expiry."""

    def __init__(self) -> None:
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _fetch() -> AccessToken:
        az = shutil.which("az")
        if not az:
            raise RuntimeError("Azure CLI not found on PATH")
        out = subprocess.run(
            [az, "account", "get-access-token", "--resource", "https://cognitiveservices.azure.com",
             "-o", "json"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        data = json.loads(out)
        if "expires_on" in data:
            expires = int(data["expires_on"])
        else:
            expires = int(datetime.fromisoformat(data["expiresOn"]).timestamp())
        return AccessToken(data["accessToken"], expires)

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        async with self._lock:
            if self._token is None or self._token.expires_on - time.time() < 300:
                self._token = await asyncio.to_thread(self._fetch)
            return self._token

    async def close(self) -> None:
        return None


def _usage(payload: dict) -> tuple[int, int]:
    usage = payload.get("usage") or {}
    inp = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(inp), int(out)


class DecisionHTTP(DecisionClient):
    def __init__(self, credential, http: httpx.AsyncClient, budget: Budget,
                 endpoint: str = RESOURCE, deployment: str = DECISION_DEPLOYMENT):
        super().__init__(endpoint, deployment, credential, http)
        self.budget = budget

    async def ask(self, state: dict, questions: dict, purpose: str = "step") -> dict:
        body = {"model": self.deployment, "state": state, "questions": questions}
        last: Exception | None = None
        for attempt in range(2):
            self.budget.reserve("decision", purpose=purpose, attempt=attempt)
            started = time.perf_counter()
            try:
                token = await self.credential.get_token(TOKEN_SCOPE)
                async with asyncio.timeout(30):
                    resp = await self.http.post(
                        self.url, headers={"Authorization": "Bearer " + token.token},
                        json=body, timeout=25,
                    )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                if resp.is_error:
                    raise RuntimeError(f"Decision HTTP {resp.status_code}: {resp.text[:400]}")
                payload = resp.json()
            except (httpx.HTTPError, TimeoutError) as exc:
                last = exc
                if attempt == 0:
                    continue
                raise RuntimeError(f"Decision request failed: {type(exc).__name__}") from None
            inp, out = _usage(payload)
            return {
                "answers": payload["answers"],
                "latency_ms": (time.perf_counter() - started) * 1000,
                "input_tokens": inp,
                "output_tokens": out,
                "usage_raw": payload.get("usage"),
                "model": payload.get("model"),
            }
        raise RuntimeError(f"Decision request failed: {type(last).__name__}")


class ChatClient:
    """Foundry v1 chat completions: POST {resource}/openai/v1/chat/completions, model = deployment."""

    def __init__(self, credential, http: httpx.AsyncClient, budget: Budget, endpoint: str = RESOURCE):
        self.url = endpoint.rstrip("/") + "/openai/v1/chat/completions"
        self.credential = credential
        self.http = http
        self.budget = budget

    async def complete_json(self, deployment: str, system: str, user: str, schema: dict,
                            *, name: str = "reply", max_tokens: int = 600,
                            reasoning_effort: str | None = "low", purpose: str = "") -> dict:
        body: dict[str, Any] = {
            "model": deployment,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": schema}},
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        last_status = None
        for attempt in range(2):
            self.budget.reserve("llm", purpose=purpose, deployment=deployment, attempt=attempt)
            started = time.perf_counter()
            try:
                token = await self.credential.get_token(TOKEN_SCOPE)
                async with asyncio.timeout(90):
                    resp = await self.http.post(
                        self.url, headers={"Authorization": "Bearer " + token.token},
                        json=body, timeout=80,
                    )
            except (httpx.HTTPError, TimeoutError) as exc:
                if attempt == 0:
                    continue
                raise RuntimeError(f"LLM request failed: {type(exc).__name__}") from None
            last_status = resp.status_code
            if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                await asyncio.sleep(1.0)
                continue
            if resp.is_error:
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            payload = resp.json()
            try:
                content = payload["choices"][0]["message"]["content"]
                parsed = json.loads(content)
            except (KeyError, IndexError, TypeError, ValueError):
                raise RuntimeError("LLM returned no valid JSON") from None
            inp, out = _usage(payload)
            return {
                "json": parsed,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "input_tokens": inp,
                "output_tokens": out,
                "model": payload.get("model"),
            }
        raise RuntimeError(f"LLM request failed (status {last_status})")
