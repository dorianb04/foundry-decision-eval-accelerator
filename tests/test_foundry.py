from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from foundry_decision_eval.contracts import Rubric
from foundry_decision_eval.foundry import create_evaluation, create_run, register_evaluators


def test_native_decision_and_llm_evaluators_use_the_same_fields():
    project = MagicMock()
    rubric = Rubric(name="new-evaluator", question="Relevant?", type="noul")
    create_evaluation(project, "comparison", [rubric], llm_deployment="existing-judge")
    client = project.get_openai_client.return_value.__enter__.return_value
    criteria = client.evals.create.call_args.kwargs["testing_criteria"]
    assert criteria[0]["data_mapping"] == criteria[1]["data_mapping"]
    assert criteria[0]["type"] == "azure_ai_evaluator"
    create_run(project, "eval-id", [{"response": "stored"}], "smoke")
    source = client.evals.runs.create.call_args.kwargs["data_source"]
    assert source["type"] == "jsonl" and "target" not in source


def test_registration_never_overwrites_an_existing_evaluator():
    project = MagicMock()
    project.evaluators.list_versions.return_value = iter([SimpleNamespace(version="1")])
    with pytest.raises(ValueError, match="already exists"):
        register_evaluators(
            project, [Rubric(name="existing", question="Relevant?", type="noul")], "connection"
        )
    project.evaluators.create_version.assert_not_called()
