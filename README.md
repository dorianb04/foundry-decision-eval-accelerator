# Foundry Decision evaluation accelerator

A small Python package that exposes **Microsoft-Decision-1 as a Foundry endpoint evaluator**. A thin Azure Functions entry point serves several configurable rubrics. Foundry runs those alongside native LLM prompt evaluators on the same stored outputs.

## Code

```text
function_app.py    Azure Functions adapter
host.json          Function routing/runtime configuration
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

`function_app.py` adapts the same FastAPI application to Azure Functions in two lines. Authentication, scoring, rubrics, and Foundry helpers stay in the package rather than being duplicated in the Function. `host.json` preserves the `/evaluate` route without an extra `/api` prefix.

## Local use

Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[functions,foundry,dev]"
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

The current Foundry preview runtime can require `status: "Completed"` and a string `reason`, unlike the linked Learn schema's lowercase status and nullable reason. The deployment enables `FOUNDRY_PREVIEW_COMPAT=1` for that runtime: successful results use `"Completed"` and an empty reason (not an invented explanation); errors return non-2xx with the explicit error payload. The default local contract remains the documented `0.0.1` schema.

## Use in Foundry

Deploy the Function over HTTPS and configure an authenticated Foundry connection to `/evaluate`. This repository does not silently create cloud resources, permissions, credentials, or connections.

`infra/main.bicep` deploys into an **existing** resource group and reuses the existing Foundry account. It creates a 512-MB Flex Consumption Function, its plan, and private AAD-authenticated storage for the host and source package. The Function has its own managed identity and narrowly scoped inference/storage grants. Copy `infra/main.parameters.example.json` to the ignored `infra/main.parameters.json` and supply the API key as a secure deployment parameter. Publish a sanitized Python source zip with remote dependency installation; no Docker or ACR build is needed. Default maximum scale is one instance with no always-ready instances.

The storage public endpoint is disabled. A private-storage networking overlay is required before publishing: Function outbound VNet integration, a Blob private endpoint and private DNS. The evaluator's API-key HTTPS endpoint and the AAD-protected SCM publishing endpoint remain reachable by Foundry and the publishing client. The client sends the zip to SCM; the deployment service writes the package to private storage. Sending a zip is not proof that it was built, deployed, or live-verified.

### Deploy

`scripts/deploy.ps1` runs the whole sequence below in order. It needs Azure CLI (signed in, with rights to deploy into the target resource group and to assign roles on the Foundry account and the new storage account), PowerShell 5.1+ and an existing resource group and Foundry account with a `Decision-1` deployment.

```powershell
# 1. Fill the two parameter files (both stay out of Git).
#    deployerObjectId = your own object ID: az ad signed-in-user show --query id -o tsv
Copy-Item infra\main.parameters.example.json infra\main.parameters.json
Copy-Item infra\private-network.parameters.example.json infra\private-network.parameters.json

# 2. Choose the evaluator API key (never written to Git; sent to Azure in a temporary file that is deleted)
$env:EVALUATOR_API_KEY = [Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])

# 3. Check without touching Azure: compiles both templates, validates parameters, builds the package
.\scripts\deploy.ps1 -CheckOnly

# 4. Preview the core deployment (nothing is created)
.\scripts\deploy.ps1 -WhatIf

# 5. Deploy
.\scripts\deploy.ps1
```

The order matters because the storage account has no public access:

| Step | What | Command |
|---|---|---|
| 1 | Function, plan, private storage, identity, role assignments | `az deployment sub create -f infra/main.bicep -p @infra/main.parameters.json` |
| 2 | VNet, Blob private endpoint, private DNS | `az deployment group create -g <rg> -f infra/private-network.bicep -p @infra/private-network.parameters.json` |
| 3 | Function VNet integration | step 1 again, with `integrationSubnetId` from step 2 |
| 4 | Source package | `scripts/package.ps1` (writes `outputs/function.zip`: `function_app.py`, `host.json`, `requirements.txt`, `pyproject.toml`, `src/`) |
| 5 | Publish with remote dependency install | `az functionapp deployment source config-zip -g <rg> -n <function> --src outputs/function.zip --build-remote true` |

Then check readiness (`curl https://<function>.azurewebsites.net/health`), create the Foundry connection to `/evaluate` with the same API key, and register the evaluators below.

There is no `azd up`: the private-network step must run between two deployments of the core template, which a single `azd provision` cannot express. The script sends only the files listed in step 4, uses the rubrics from `examples/rubrics.json` (override with `-Rubrics`), and creates no resource group, Foundry account, key or connection.

Status: the templates compile and the package builds (`-CheckOnly`). The sequence has not been run end to end against a subscription in this repository; run `-WhatIf` first, and treat a successful upload as unproven until `/health` answers and one evaluation succeeds.

```powershell
$env:AZURE_AI_PROJECT_ENDPOINT="https://YOUR-ACCOUNT.services.ai.azure.com/api/projects/YOUR-PROJECT"
python examples\register.py --connection YOUR-CONNECTION
python examples\register.py --connection YOUR-CONNECTION --execute
python examples\evaluate.py --judge-deployment YOUR-JUDGE
python examples\evaluate.py --judge-deployment YOUR-JUDGE --execute
```

The examples are dry-run by default. Registration creates new names only and refuses to replace existing evaluators. It creates one endpoint evaluator and one native `llm-...` prompt evaluator per rubric. Both use the same criteria, fields, and normalized scale; they are different judging mechanisms, not guaranteed equivalent.

`evaluate.py --data PATH` accepts JSONL stored outputs or a JSON array/`{"items":[...]}` export. It submits a seeded subset inline with **no model or agent target**, so it never regenerates answers. To compare configurations, reuse the same row IDs and questions from each configuration's saved run. Existing datasets that contain only response IDs need their already-generated run outputs, not substituted ground truth.

The SDK helpers use `azure-ai-projects>=2.10.0`: catalog CRUD is now under `project.evaluators`, while older Learn examples use `project.beta.evaluators`. They include the service-required `Foundry-Features: Evaluations=V1Preview` opt-in header.

## Cost, latency, quality

Decision results expose observed input/output token usage and `wall_time_ms` for authentication, HTTP, inference, and parsing. This is **judge latency**, not original answer-generation latency. Native LLM evaluator outputs are retained by Foundry; compare their recorded usage/timing against Decision on the same rows and rubrics.

Apply the current deployment/region token prices to observed usage, and report Function/storage/logging costs separately. Missing prices or timing remain unknown, not zero. Model agreement is not quality ground truth: validate disagreements against independent human-reviewed labels before drawing quality conclusions. Probabilities are not claimed to be calibrated, and five smoke rows cannot establish a ranking.

## Checks and references

```powershell
python -m pytest -q
python -m ruff check src tests examples function_app.py
```

- [Decision API](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision)
- [Foundry endpoint-evaluator contract](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators#endpoint-based-evaluators)
- [Native cloud evaluation SDK](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation)
- [Functions evaluator reference supplied for this accelerator](https://github.com/Jbetts05/foundry-evaluation-sample)
