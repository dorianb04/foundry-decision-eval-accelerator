from __future__ import annotations

from typing import Any


def export_run_selection(
    client: Any,
    selection: dict[str, Any],
    *,
    eval_id: str,
    run_id: str,
    max_scan: int = 200,
) -> dict[str, Any]:
    dataset_id = selection["provenance"].get("registered_dataset_id")
    if not dataset_id:
        raise ValueError("Frozen selection must record a registered_dataset_id")
    run = client.evals.runs.retrieve(eval_id=eval_id, run_id=run_id)
    if run.status != "completed":
        raise ValueError("Only completed stored runs may supply responses")
    data_source = run.data_source
    source = (
        data_source.model_dump(mode="json") if hasattr(data_source, "model_dump") else data_source
    )
    if source.get("source", {}).get("id") != dataset_id:
        raise ValueError("Run's registered dataset does not match frozen selection")
    selected_items = selection["items"]
    if not 1 <= len(selected_items) <= 10:
        raise ValueError("Stored-output selections require 1..10 rows")
    ids = [item["id"] for item in selected_items]
    if len(set(ids)) != len(ids):
        raise ValueError("Selected dataset ids must be unique")
    wanted = set(ids)
    found: dict[str, Any] = {}
    scanned = 0
    page = client.evals.runs.output_items.list(eval_id=eval_id, run_id=run_id, limit=50)
    for output in page:
        scanned += 1
        if scanned > max_scan:
            break
        value = output.model_dump(mode="json")
        item = value.get("datasource_item", {})
        row_id = item.get("id")
        if row_id not in wanted:
            continue
        if row_id in found:
            raise ValueError("Stored run returned a duplicate selected dataset id")
        trace = item.get("sample.output_items")
        response_text = item.get("sample.output_text")
        if (
            not isinstance(trace, list)
            or not trace
            or not isinstance(response_text, str)
            or not response_text.strip()
            or value.get("sample", {}).get("error") is not None
        ):
            raise ValueError("Selected stored output lacks a successful response and full trace")
        found[row_id] = {
            "item": {
                "id": row_id,
                "query": item["query"],
                "response": trace,
                "response_text": response_text,
            },
            "output_item_id": value["id"],
            "existing_results": [
                {key: result.get(key) for key in ("name", "metric", "score", "status", "threshold")}
                for result in value.get("results", [])
            ],
        }
        if len(found) == len(wanted):
            break
    if set(found) != wanted:
        raise ValueError(f"Only {len(found)}/{len(wanted)} selected ids found within scan bound")
    for original in selected_items:
        if original["query"] != found[original["id"]]["item"]["query"]:
            raise ValueError("Stored query does not match the selected dataset row")
    return {
        "items": [found[row_id]["item"] for row_id in ids],
        "provenance": {
            **selection["provenance"],
            "source_eval_id": eval_id,
            "source_run_id": run_id,
            "source_run_name": run.name,
            "target": source.get("target"),
            "source_output_item_ids": [found[row_id]["output_item_id"] for row_id in ids],
            "response_mapping": "datasource_item['sample.output_items']; complete stored trace",
            "stored_rows_scanned": scanned,
            "new_responses_generated": 0,
        },
        "existing_results": [found[row_id]["existing_results"] for row_id in ids],
    }
