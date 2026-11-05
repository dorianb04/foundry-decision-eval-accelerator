"""Register Decision endpoint evaluators and equivalent native LLM prompt evaluators."""

import argparse
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from foundry_decision_eval.evaluators import load_rubrics
from foundry_decision_eval.foundry import register_evaluators

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--connection", required=True, help="Existing authenticated Foundry connection")
parser.add_argument("--config", default="examples/rubrics.json")
parser.add_argument("--execute", action="store_true", help="Explicitly create new catalog entries")
args = parser.parse_args()
rubrics = load_rubrics(args.config)
if not args.execute:
    print({"decision": [r.name for r in rubrics], "llm": ["llm-" + r.name for r in rubrics]})
else:
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"], credential=credential
        ) as project,
    ):
        print(register_evaluators(project, rubrics, args.connection))
