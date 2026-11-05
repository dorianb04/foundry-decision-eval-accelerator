# Foundry Decision evaluation accelerator

Score existing outputs with **Microsoft-Decision-1**, optionally compare an LLM
judge on exactly the same rows, and expose the scorer as a Microsoft Foundry
**endpoint-based evaluator**. This is a Python-first preview accelerator, not an
agent-generation pipeline.

## What works

- Decision API adapter for `score`, `noul`, and `choice`, using the deployment
  name and Entra authentication. Decision-1 is **not** a chat-completions model.
- FastAPI endpoint implementing Foundry request/response schema `0.0.1`, explicit
  errors, API-key authentication, and a **25-second total inference budget**
  inside Foundry's documented 30-second timeout. There are no inference retries.
- Frozen, seeded subsets of 1–10 rows with SHA-256, source row indices, field
  mappings, and optional registered-dataset provenance.
- Bounded Foundry asset discovery, private JSONL downloads, evaluator export,
  and reuse of **already-generated run outputs** with exact dataset/id/query
  matching. This includes complete stored traces (system instructions, tool
  evidence, and final answer), when the existing run contains them.
- Opt-in local LLM-as-judge comparison; cloud evaluator registration and
  dataset-only runs are **dry-run by default**.

No Azure infrastructure, identity, permission, connection, dataset, or agent is
created automatically. Cloud integration requires an authenticated hosted
endpoint and an existing Foundry connection; running locally does not prove a
native cloud evaluation.

## Install

Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
foundry-eval demo
```

`demo` only replays a labeled synthetic Decision response fixture. It makes no
network requests and does not infer a score for arbitrary data.

On Windows ARM64, if a dependency tries to compile without MSVC, use an existing
x64 Python installation (`py -3.12 -m venv .venv`) rather than installing build
tools globally.

## Configure existing Foundry access

Use your existing Azure CLI sign-in. Do not change subscription just to access a
project: project data-plane authorization is independent of whether the current
subscription can resolve its resource group.

```powershell
$env:AZURE_AI_PROJECT_ENDPOINT="https://YOUR-ACCOUNT.services.ai.azure.com/api/projects/YOUR-PROJECT"
$env:AZURE_AI_RESOURCE_ENDPOINT="https://YOUR-ACCOUNT.services.ai.azure.com"
$env:DECISION_DEPLOYMENT="Decision-1"
$env:AZURE_CREDENTIAL_MODE="cli"
foundry-eval discover --limit 10
```

Project calls use the project SDK audience. Decision and optional LLM inference
use `https://cognitiveservices.azure.com/.default`. The Decision resource URL must
be an HTTPS origin: never append its API path to `/api/projects/...`.

Use `AZURE_CREDENTIAL_MODE=default` for `DefaultAzureCredential` on a host with
an **already-authorized** managed identity or other configured credential.
This tool never creates that access. `.env.example` documents the variables;
environment files are not automatically loaded.

The implementation requires `azure-ai-projects>=2.10.0,<3`. Microsoft Learn's
endpoint example uses `project_client.beta.evaluators`; the verified 2.10 SDK
has moved catalog CRUD to **`project_client.evaluators`**. The endpoint definition
and evaluation HTTP contracts remain those in the Learn reference.

## Freeze and score an existing dataset

Downloads, selections, live rubric exports, and reports belong in ignored
`.local/` or `outputs/`, **never in Git**, even in a private repository.

```powershell
foundry-eval fetch-dataset --name YOUR-DATASET --version 1 --out .local\dataset.jsonl
foundry-eval inspect-dataset --dataset .local\dataset.jsonl
foundry-eval select --dataset .local\dataset.jsonl --mapping examples\mapping.json --limit 5 --seed 42 --out .local\selection.json
foundry-eval score --selection .local\selection.json --rubric examples\rubric.json --out outputs\decision.json
```

`examples/mapping.json` maps canonical evaluator fields to dataset fields.
Nested object paths and literal keys containing dots are supported; values,
including conversation arrays, are preserved. Missing fields are errors, never
empty-string defaults. Only map the evidence needed by your rubric. For a fully
synthetic live smoke test, replace the dataset path with
`examples\synthetic.jsonl`.

To compare an LLM judge, add `--judge-deployment YOUR-EXISTING-JUDGE-DEPLOYMENT`
to `score`. Both models receive the **same frozen item and rubric**. Reports
retain paired results and mean absolute score difference over completed pairs;
failed judgments are excluded and counted, never converted into scores.
Agreement is descriptive, not proof either judge is correct.

The original dataset is read to choose a uniform reproducible subset; only the
selected rows are sent for inference. JSONL downloads are capped at 64 MiB.
Downloads and all report writes refuse to overwrite existing files.

## Reuse stored answers across configurations

Some registered datasets contain only queries, references, and response IDs.
Do **not** substitute ground truth for an answer or rerun an agent implicitly.
Instead, export saved traces from completed evaluation runs on that exact
registered dataset:

```powershell
foundry-eval select --dataset .local\dataset.jsonl --mapping examples\id-mapping.json --dataset-id "azureai://accounts/ACCOUNT/projects/PROJECT/data/DATASET/versions/1" --limit 5 --seed 42 --out .local\row-selection.json
foundry-eval export-run --selection .local\row-selection.json --eval-id YOUR-EVAL-ID --run-id YOUR-RUN-ID --out .local\config-a.json
foundry-eval score --selection .local\config-a.json --rubric .local\reviewed-rubric.json --out outputs\config-a.json
```

Repeat the last two commands with each configuration's run ID, always using the
same row-selection file. The exporter checks dataset identity, unique row IDs,
matching query text, and presence of both a successful answer and complete
stored trace. It scans at most 200 stored output records to locate the chosen
IDs and persists only selected traces. New agent/model answers are not generated.

Read an existing evaluator without modifying it:

```powershell
foundry-eval export-evaluator --name YOUR-EVALUATOR --version 2 --out .local\existing-evaluator.json
```

Adapt its rubric into the shape of `examples/rubric.json` in an ignored file.
Keep its substantive instructions, map the original levels from lowest to
highest, remove any generative JSON-output instructions, and replace input
placeholders with references to `item.query`, `item.response`, etc. Review this
adaptation: changing a prompt evaluator to a decision model is **not** an
assertion that their scores are interchangeable. Original prompt-evaluator
scores can have different inputs and must not be presented as a paired judge
comparison without checking their mappings.

## Score semantics and limitations

| Primitive | Endpoint score (higher is better) | Retained evidence |
|---|---|---|
| `score` | `100 * raw_score / (number_of_levels - 1)` | Raw weighted level index and level probabilities |
| `noul` | `100 * noul` | Raw probability of the positive proposition |
| `choice` | `100 * probability[positive_label]` | Selected label and every option probability |

Rubric thresholds are **integers from 0 to 100** to match Foundry's endpoint
response contract; `passed` is `score >= threshold`. Use `null` to disable a
threshold. Probabilities are **not claimed to be calibrated**. Decision-1 gives
no written rationale, so `reason` is `null`; raw answers are in `properties`.
Nonfinite, malformed, out-of-range, or inconsistent responses become explicit
errors with null scores and pass flags. Upstream response bodies are not echoed.
Errors yield a nonzero batch CLI exit status and remain in reports.

The three-level example is groundedness **against supplied evidence**, not an
external factuality check. Adapt rubrics to the task, preserve complete evidence,
and validate with labeled examples before operational thresholds. Small smoke
subsets cannot establish model rankings. Do not use this as an autonomous
high-stakes decision-maker.

## Run the endpoint locally

```powershell
foundry-eval serve --rubric examples\rubric.json --allow-anonymous-local
```

This binds to loopback only. `GET /health` checks readiness. `POST /evaluate`
accepts:

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

`data.sample` is optional and preserved if present. For conversation scoring,
configure the rubric's required fields accordingly and send
`evaluation_level=conversation`. The endpoint accepts one evaluation item per
request, not an undocumented batch envelope.

For externally reachable hosting, supply `EVALUATOR_API_KEY` from an existing
secret store and require HTTPS at the ingress. The default inbound key header is
`api-key`; override `EVALUATOR_API_KEY_HEADER` to match your configured Foundry
connection's actual header. Anonymous mode cannot bind to a non-loopback host.
The key is never generated or logged by this project.

## Register and run in Foundry (explicit opt-in)

First obtain approval for hosting and creating/configuring a Foundry connection
if those do not already exist. Supported connection authentication is documented
in the [official endpoint-evaluator guide](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators#endpoint-based-evaluators).
This app implements API-key authentication. For Entra inbound authentication,
configure and validate Easy Auth/equivalent at the host; the app does not treat an
unvalidated bearer header as authentication.

Registration plans are local and do not need Azure credentials:

```powershell
foundry-eval register --name decision-groundedness --connection YOUR-EXISTING-CONNECTION
```

Only after reviewing the plan and the connection's URL/authentication:

```powershell
foundry-eval register --name decision-groundedness --connection YOUR-EXISTING-CONNECTION --execute
foundry-eval cloud-run --selection .local\selection.json --evaluator decision-groundedness --out outputs\cloud.json
foundry-eval cloud-run --selection .local\selection.json --evaluator decision-groundedness --out outputs\cloud.json --execute
```

`register` refuses to append a version to an existing evaluator name. Configure
the hosted rubric's name/version to match the returned registration. `cloud-run`
creates a new **dataset-only** eval with `azure_ai_evaluator` criteria and
`{{item.FIELD}}` mappings, then submits the frozen rows inline. It never uploads
a replacement dataset or configures a target to generate answers.

To reuse a compatible existing eval, pass `--existing-eval-id ID`. Definitions
that map `sample.*` are rejected because a dataset-only run cannot satisfy their
target-output contract. Nothing overwrites the original eval. Polling is bounded
(`--wait-seconds`, default 300); a timeout reports the run ID without claiming
the run stopped. Do not resubmit it blindly.

**Local scoring alone is not native Foundry integration.** If the host or
connection is missing, registration and cloud execution remain pending.

## Checks and references

```powershell
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
```

- [Microsoft-Decision-1 API and score semantics](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision)
- [Endpoint-based evaluators and 30-second contract](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators#endpoint-based-evaluators)
- [Cloud evaluation SDK workflow](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation)
- [Official custom-evaluator sample](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/ai/azure-ai-projects/samples/evaluations/sample_eval_catalog.py)
