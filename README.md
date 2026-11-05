# Foundry Decision evaluation accelerator

A small Python package that exposes **Microsoft-Decision-1 as a Foundry endpoint evaluator**. One container serves several configurable rubrics. Foundry runs those alongside native LLM prompt evaluators on the same stored outputs.

## Code

```text
src/foundry_decision_eval/
  main.py         FastAPI endpoint and authentication
  decision.py     Decision API client and score conversion
  contracts.py    Foundry request schema and rubric model
  evaluators.py   Load rubric configuration
  foundry.py      Register evaluators and create native Foundry runs
examples/
  rubrics.json    Groundedness, relevance, completeness
  register.py     Register endpoint + matching LLM prompt evaluators
  evaluate.py     Evaluate a small seeded subset of existing outputs
```

No separate evaluation framework, custom dataset registry, or agent-generation pipeline. Foundry owns runs and results.

## Local use

Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[foundry,dev]"
$env:AZURE_AI_RESOURCE_ENDPOINT="https://YOUR-ACCOUNT.services.ai.azure.com"
$env:DECISION_DEPLOYMENT="Decision-1"
$env:AZURE_CREDENTIAL_MODE="cli"
$env:EVALUATOR_API_KEY="your-local-development-key"
python -m foundry_decision_eval.main
```

Use your existing Azure CLI sign-in locally. Hosted deployments use `DefaultAzureCredential` with an already-authorized managed identity. Do not put keys, live data, exported rubrics, or results in Git; use ignored `.local/` and `outputs/`.

The service listens on port 8080 (`PORT` overrides it). `GET /health` checks readiness. `POST /evaluate` requires the `api-key` header (`EVALUATOR_API_KEY_HEADER` overrides the header name).

```json
{
  "schema_version": "0.0.1",
  "evaluator_name": "decision-groundedness",
  "evaluator_version": "1",
  "evaluation_level": "turn",
  "data": {
    "item": {
      "query": "What is the capital of France?",
      "response": "Paris.",
      "context": "Paris is the capital of France."
    }
  }
}
```

## Configure evaluators

Set `EVALUATORS_CONFIG` to a JSON rubric file, or `EVALUATORS_CONFIG_JSON` to its JSON content. See `examples/rubrics.json`. Each rubric has a name, version, question, Decision primitive, criteria, required mapped fields, and integer threshold on 0–100.

| Primitive | Returned score |
|---|---|
| `score` | `100 * weighted_level_index / (number_of_levels - 1)` |
| `noul` | `100 * probability_of_true` |
| `choice` | `100 * probability_of_positive_label` |

Decision-1 is **not chat completions**: the client calls `/providers/microsoft/v1/systemone` on the resource origin with your deployment name and Entra audience `https://cognitiveservices.azure.com`. It returns no written rationale: `reason` is null, with raw scores/probabilities in `properties`. Errors have null scores and cannot pass. Total inference is bounded to 25 seconds inside Foundry's documented 30-second deadline.

## Use in Foundry

Host the container over HTTPS and configure an authenticated Foundry connection to `/evaluate`. This repository does not silently create cloud resources, permissions, credentials, or connections.

`infra/Dockerfile` packages only the runtime dependencies, not the optional Foundry SDK. `infra/main.bicep` deploys into an **existing** resource group, Container Apps Consumption environment, and registry. Copy `infra/main.parameters.example.json` to the ignored `infra/main.parameters.json`, supply the API key as a secure deployment parameter, and pass a real versioned image. The template creates only a dedicated identity, its two resource-scoped grants, and the evaluator app. Default scale is 0–1 replicas; warm it during evaluations to avoid cold-start timeouts.

```powershell
$env:AZURE_AI_PROJECT_ENDPOINT="https://YOUR-ACCOUNT.services.ai.azure.com/api/projects/YOUR-PROJECT"
python examples\register.py --connection YOUR-CONNECTION
python examples\register.py --connection YOUR-CONNECTION --execute
python examples\evaluate.py --judge-deployment YOUR-JUDGE
python examples\evaluate.py --judge-deployment YOUR-JUDGE --execute
```

The examples are dry-run by default. Registration creates new names only and refuses to replace existing evaluators. It creates one endpoint evaluator and one native `llm-...` prompt evaluator per rubric. Both use the same criteria, fields, and normalized scale; they are different judging mechanisms, not guaranteed equivalent.

`evaluate.py --data PATH` accepts JSONL stored outputs or a JSON array/`{"items":[...]}` export. It submits a seeded subset inline with **no model or agent target**, so it never regenerates answers. To compare configurations, reuse the same row IDs and questions from each configuration's saved run. Existing datasets that contain only response IDs need their already-generated run outputs, not substituted ground truth.

The SDK helpers use `azure-ai-projects>=2.10.0`: catalog CRUD is now under `project.evaluators`, while older Learn examples use `project.beta.evaluators`.

## Cost, latency, quality

Decision results expose observed input/output token usage and `wall_time_ms` for authentication, HTTP, inference, and parsing. This is **judge latency**, not original answer-generation latency. Native LLM evaluator outputs are retained by Foundry; compare their recorded usage/timing against Decision on the same rows and rubrics.

Apply the current deployment/region token prices to observed usage, and report container/logging/build costs separately. Missing prices or timing remain unknown, not zero. Model agreement is not quality ground truth: validate disagreements against independent human-reviewed labels before drawing quality conclusions. Probabilities are not claimed to be calibrated, and five smoke rows cannot establish a ranking.

## Checks and references

```powershell
python -m pytest -q
python -m ruff check src tests examples
```

- [Decision API](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision)
- [Foundry endpoint-evaluator contract](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators#endpoint-based-evaluators)
- [Native cloud evaluation SDK](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation)
