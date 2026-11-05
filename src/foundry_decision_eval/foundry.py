from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ResourceNotFoundError


def discover(project: AIProjectClient, limit: int = 10) -> dict[str, Any]:
    if not 1 <= limit <= 50:
        raise ValueError("Metadata limit must be in [1, 50]")

    def metadata(items: Any) -> list[dict[str, Any]]:
        return [
            {
                key: value
                for key in ("id", "name", "version", "display_name")
                if (value := getattr(item, key, None)) is not None
            }
            for item in islice(items, limit)
        ]

    with project.get_openai_client() as client:
        evaluations = [
            {"id": item.id, "name": item.name} for item in client.evals.list(limit=limit).data
        ]
    return {
        "limit_per_collection": limit,
        "datasets": metadata(project.datasets.list()),
        "deployments": metadata(project.deployments.list()),
        "evaluators": metadata(project.evaluators.list(type="custom", limit=limit)),
        "evaluations": evaluations,
        "note": "Bounded metadata only; no dataset content or credentials included",
    }


def download_dataset(
    project: AIProjectClient,
    name: str,
    version: str,
    output: Path,
    *,
    blob_name: str | None = None,
    max_bytes: int = 64 * 1024 * 1024,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("Refusing to overwrite an existing dataset cache")
    dataset = project.datasets.get(name=name, version=version)
    if dataset.type != "uri_file":
        raise ValueError("Only existing single-file JSONL datasets are supported")
    reference = project.datasets.get_credentials(name=name, version=version).blob_reference
    signed = urlsplit(reference.credential.sas_uri)
    if signed.scheme != "https" or not signed.hostname or signed.fragment:
        raise ValueError("Foundry returned an invalid HTTPS storage reference")
    owned_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False)
    partial = output.with_name(output.name + ".partial")
    created_partial = False
    try:
        query = dict(parse_qsl(signed.query))
        query.update(restype="container", comp="list", maxresults="100")
        listing = client.get(urlunsplit(signed._replace(query=urlencode(query))))
        if listing.status_code != 200:
            raise RuntimeError(f"Dataset listing returned HTTP {listing.status_code}")
        root = ET.fromstring(listing.content)
        names = [entry.text for entry in root.findall("./Blobs/Blob/Name") if entry.text]
        if blob_name is None:
            candidates = [name for name in names if name.lower().endswith(".jsonl")]
            if root.findtext("NextMarker") or len(candidates) != 1:
                raise ValueError("Specify --blob-name: container does not identify one JSONL file")
            blob_name = candidates[0]
        elif blob_name.startswith("/") or ".." in blob_name.split("/"):
            raise ValueError("blob_name must be a relative storage object name")
        url = urlunsplit(
            signed._replace(path=signed.path.rstrip("/") + "/" + quote(blob_name, safe="/"))
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Dataset download returned HTTP {response.status_code}")
            with partial.open("xb") as stream:
                created_partial = True
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Dataset exceeds the 64 MiB smoke-download limit")
                    stream.write(chunk)
        output.hardlink_to(partial)
    except (httpx.RequestError, ET.ParseError) as exc:
        raise RuntimeError(
            "Dataset storage transfer failed; credentials and URLs suppressed"
        ) from exc
    finally:
        if created_partial and partial.exists():
            partial.unlink()
        if owned_client:
            client.close()
    return {"dataset_id": dataset.id, "name": name, "version": version, "blob_name": blob_name}


def endpoint_definition(name: str, connection_name: str) -> dict[str, Any]:
    if not name or not connection_name:
        raise ValueError("Evaluator name and an existing connection name are required")
    return {
        "name": name,
        "categories": ["quality"],
        "display_name": name,
        "description": "Bounded Microsoft-Decision-1 endpoint evaluator; score 0..100",
        "definition": {"type": "endpoint", "connection_name": connection_name},
    }


def register_endpoint(project: AIProjectClient, name: str, connection_name: str) -> Any:
    # Never silently append a version to a catalog entry someone already owns.
    try:
        next(iter(project.evaluators.list_versions(name=name)))
    except ResourceNotFoundError:
        pass
    except StopIteration:
        pass
    else:
        raise ValueError("Evaluator already exists; choose a new name (no update performed)")
    return project.evaluators.create_version(
        name=name, evaluator_version=endpoint_definition(name, connection_name)
    )


def testing_criterion(
    name: str,
    fields: list[str],
    *,
    initialization_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    criterion: dict[str, Any] = {
        "type": "azure_ai_evaluator",
        "name": name,
        "evaluator_name": name,
        "data_mapping": {field: "{{item." + field + "}}" for field in fields},
    }
    if initialization_parameters is not None:
        criterion["initialization_parameters"] = initialization_parameters
    return criterion


def run_cloud(
    project: AIProjectClient,
    items: list[dict[str, Any]],
    evaluator_name: str,
    *,
    existing_eval_id: str | None = None,
    wait_seconds: float = 300,
) -> dict[str, Any]:
    if not items or len(items) > 10:
        raise ValueError("Cloud smoke runs require 1..10 selected rows")
    with project.get_openai_client() as client:
        if existing_eval_id:
            evaluation = client.evals.retrieve(existing_eval_id)
            for criterion in evaluation.testing_criteria:
                mapping = (
                    criterion.get("data_mapping", {})
                    if isinstance(criterion, dict)
                    else getattr(criterion, "data_mapping", {})
                )
                if any("sample." in str(value) for value in mapping.values()):
                    raise ValueError(
                        "Existing eval requires target samples; select a dataset-only definition"
                    )
        else:
            fields = sorted(set.intersection(*(set(item) for item in items)))
            evaluation = client.evals.create(
                name=f"{evaluator_name}-dataset-smoke",
                data_source_config={
                    "type": "custom",
                    "item_schema": {
                        "type": "object",
                        "properties": {field: {} for field in fields},
                        "required": fields,
                    },
                    "include_sample_schema": False,
                },
                testing_criteria=[testing_criterion(evaluator_name, fields)],
            )
        run = client.evals.runs.create(
            eval_id=evaluation.id,
            name="selected-existing-outputs",
            data_source={
                "type": "jsonl",
                "source": {
                    "type": "file_content",
                    "content": [{"item": item} for item in items],
                },
            },
        )
        deadline = time.monotonic() + wait_seconds
        while run.status not in {"completed", "failed", "canceled"}:
            if time.monotonic() >= deadline:
                return {
                    "eval_id": evaluation.id,
                    "run_id": run.id,
                    "status": run.status,
                    "polling_timed_out": True,
                    "note": "Run remains active; retrieve it by id. Do not blindly resubmit.",
                }
            time.sleep(5)
            run = client.evals.runs.retrieve(eval_id=evaluation.id, run_id=run.id)
        return {
            "eval_id": evaluation.id,
            "run_id": run.id,
            "status": run.status,
            "report_url": run.report_url,
            "output_items": [
                item.model_dump(mode="json")
                for item in client.evals.runs.output_items.list(
                    eval_id=evaluation.id, run_id=run.id, limit=10
                )
            ],
        }
