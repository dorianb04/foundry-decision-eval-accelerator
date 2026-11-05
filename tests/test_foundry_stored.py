import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from azure.core.exceptions import ResourceNotFoundError

from foundry_decision_eval.foundry import (
    download_dataset,
    endpoint_definition,
    register_endpoint,
    run_cloud,
)
from foundry_decision_eval.foundry import (
    testing_criterion as criterion,
)
from foundry_decision_eval.stored import export_run_selection


def test_endpoint_registration_shape_and_duplicate_protection():
    project = MagicMock()
    project.evaluators.list_versions.return_value = iter([{"version": "1"}])
    with pytest.raises(ValueError, match="already exists"):
        register_endpoint(project, "endpoint-evaluator", "existing-connection")
    project.evaluators.create_version.assert_not_called()
    project.evaluators.list_versions.side_effect = ResourceNotFoundError()
    register_endpoint(project, "endpoint-evaluator", "existing-connection")
    project.evaluators.create_version.assert_called_once_with(
        name="endpoint-evaluator",
        evaluator_version=endpoint_definition("endpoint-evaluator", "existing-connection"),
    )
    assert endpoint_definition("e", "c")["definition"] == {
        "type": "endpoint",
        "connection_name": "c",
    }


def test_dataset_only_mapping():
    assert criterion("decision", ["query", "response"])["data_mapping"] == {
        "query": "{{item.query}}",
        "response": "{{item.response}}",
    }


def project_with_storage():
    project = MagicMock()
    project.datasets.get.return_value = SimpleNamespace(type="uri_file", id="dataset-id")
    project.datasets.get_credentials.return_value = SimpleNamespace(
        blob_reference=SimpleNamespace(
            credential=SimpleNamespace(
                sas_uri="https://storage.test/container?sig=private-test-sig"
            )
        )
    )
    return project


def test_private_download_and_no_overwrite(tmp_path):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.params.get("comp") == "list":
            return httpx.Response(
                200,
                text=(
                    "<EnumerationResults><Blobs><Blob><Name>selected.jsonl</Name></Blob></Blobs>"
                    "<NextMarker /></EnumerationResults>"
                ),
            )
        return httpx.Response(200, content=b'{"id":"synthetic"}\n')

    out = tmp_path / "dataset.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_dataset(project_with_storage(), "existing", "1", out, client=client)
        assert out.read_text() == '{"id":"synthetic"}\n'
        assert "private-test-sig" not in json.dumps(result)
        assert "sig=private-test-sig" in seen[-1]
        with pytest.raises(FileExistsError):
            download_dataset(project_with_storage(), "existing", "1", out, client=client)


def test_partial_download_is_cleaned_and_does_not_log_sas(tmp_path):
    def handler(req):
        if req.url.params.get("comp") == "list":
            return httpx.Response(
                200,
                text=(
                    "<EnumerationResults><Blobs><Blob><Name>data.jsonl</Name></Blob></Blobs>"
                    "</EnumerationResults>"
                ),
            )
        return httpx.Response(200, content=b"too large")

    out = tmp_path / "dataset.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="limit"):
            download_dataset(
                project_with_storage(), "existing", "1", out, client=client, max_bytes=1
            )
    assert not out.exists() and not out.with_name("dataset.jsonl.partial").exists()


def test_cloud_run_uses_inline_existing_outputs_not_a_generation_target():
    project = MagicMock()
    client = project.get_openai_client.return_value.__enter__.return_value
    client.evals.create.return_value = SimpleNamespace(id="eval-id")
    client.evals.runs.create.return_value = SimpleNamespace(
        id="run-id",
        status="completed",
        report_url="https://example.test/report",
    )
    client.evals.runs.output_items.list.return_value = []
    result = run_cloud(project, [{"query": "q", "response": "already stored"}], "endpoint")
    assert result["status"] == "completed"
    body = client.evals.runs.create.call_args.kwargs
    assert body["data_source"] == {
        "type": "jsonl",
        "source": {
            "type": "file_content",
            "content": [
                {"item": {"query": "q", "response": "already stored"}},
            ],
        },
    }
    assert "target" not in body["data_source"]


def test_existing_target_eval_cannot_be_misused_for_dataset_run():
    project = MagicMock()
    client = project.get_openai_client.return_value.__enter__.return_value
    client.evals.retrieve.return_value = SimpleNamespace(
        testing_criteria=[
            {"data_mapping": {"response": "{{sample.output_text}}"}},
        ]
    )
    with pytest.raises(ValueError, match="target samples"):
        run_cloud(project, [{"query": "q"}], "endpoint", existing_eval_id="existing")
    client.evals.runs.create.assert_not_called()


class Output:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **_kwargs):
        return self.value


def stored_client(dataset_id="dataset-id", response=True):
    client = MagicMock()
    client.evals.runs.retrieve.return_value = SimpleNamespace(
        name="synthetic-config",
        status="completed",
        data_source={"source": {"id": dataset_id}, "target": {"name": "synthetic-agent"}},
    )
    client.evals.runs.output_items.list.return_value = [
        Output(
            {
                "id": "output-id",
                "datasource_item": {
                    "id": "row-a",
                    "query": "query-a",
                    "sample.output_text": "stored answer" if response else "",
                    "sample.output_items": [
                        {"role": "system", "content": "synthetic system"},
                        {"role": "tool", "content": "synthetic evidence"},
                        {"role": "assistant", "content": "stored answer"},
                    ],
                },
                "sample": {"error": None},
                "results": [],
            }
        ),
    ]
    return client


def selection():
    return {
        "items": [{"id": "row-a", "query": "query-a"}],
        "provenance": {"registered_dataset_id": "dataset-id", "seed": 42},
    }


def test_stored_trace_reuse_preserves_pairing_and_provenance():
    result = export_run_selection(stored_client(), selection(), eval_id="eval", run_id="run")
    assert result["items"][0]["response"][0]["role"] == "system"
    assert result["items"][0]["response"][-1]["content"] == "stored answer"
    assert result["provenance"]["new_responses_generated"] == 0
    assert result["provenance"]["source_output_item_ids"] == ["output-id"]


@pytest.mark.parametrize("kind", ["dataset", "query", "response", "missing"])
def test_stored_trace_reuse_rejects_mismatches(kind):
    client = stored_client(
        dataset_id="wrong-dataset" if kind == "dataset" else "dataset-id",
        response=kind != "response",
    )
    selected = selection()
    if kind == "query":
        selected["items"][0]["query"] = "different"
    if kind == "missing":
        selected["items"][0]["id"] = "absent"
    with pytest.raises(ValueError):
        export_run_selection(client, selected, eval_id="eval", run_id="run")
