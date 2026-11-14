# Caldova setup and deployment

Use PowerShell 7 from the repository root. Azure CLI must be signed in as a user
who can create resources, assign roles, and accept model/Marketplace terms.
Install the root `requirements.txt` into `.venv` with `uv` (see the root README;
point `uv` at your organization's package feed if direct PyPI downloads are blocked).
No local Docker is used.

The policy PDFs in `data/documents/` are committed. After editing a Markdown source,
regenerate them before running coordinator setup:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_documents.py
```

The subscription must be **Enabled**. Every live script checks its ARM subscription
state before making writes. Offline verification is safe while the subscription is
disabled:

```powershell
.\scripts\verify_offline.ps1
```

The verifier prints the installed SDK signatures, validates generated model fields
and serialization, compiles all setup scripts/Bicep, and runs offline helper and
mocked provisioning tests. It never authenticates the SDK or calls Azure resource APIs.
The only Bicep type warning expected is `BCP037` for `modelProviderData`, which the
provider requires but this Bicep type catalog does not declare.

Verified SDK surface (`azure-ai-projects==2.10.0`):
- `AIProjectClient(..., allow_preview=True)`, `get_openai_client(agent_name=...)`.
- `agents.create_version`, `get_version`, `get`, `update_details`, `delete`;
  `toolboxes.create_version`.
- `HostedAgentDefinition`, `ContainerConfiguration`, `ProtocolVersionRecord`,
  `WebSearchToolboxTool`, `CodeInterpreterToolboxTool`, `AutoCodeInterpreterToolParam`.
- `AgentEndpointConfig`, `VersionSelector`, `FixedRatioVersionSelectionRule`,
  `ProtocolConfiguration`, `ResponsesProtocolConfiguration`, `A2AProtocolConfiguration`,
  `AgentCard`, `AgentCardSkill`.
- `PromptAgentDefinition`, `FileSearchTool`, `A2ATool`, `A2AProtocolVersion.V1_0`;
  response identity: `AgentVersionDetails.instance_identity.principal_id`
  (with `AgentDetails.instance_identity` fallback).
- `DefaultAzureCredential.get_token`; OpenAI `vector_stores.create/retrieve`,
  `vector_stores.files.list/upload_and_poll/delete`, `responses.create(stream=True)`.

Run either the complete sequence:

```powershell
.\scripts\deploy_all.ps1
```

or its individual steps:

```powershell
.\scripts\provision.ps1
.\scripts\deploy_analyst.ps1
.\.venv\Scripts\python.exe .\scripts\setup_coordinator.py
.\.venv\Scripts\python.exe .\scripts\smoke_test.py
.\.venv\Scripts\python.exe .\scripts\setup_evaluations.py
```

Use `-NoClaude` on `provision.ps1` or `deploy_all.ps1` to choose the OpenAI analyst
fallback explicitly (recommended on internal or sandbox subscriptions, where Azure
Marketplace blocks partner-model purchases). Otherwise Claude is requested, and a
failed foundation deployment is retried once without it.

`-CoordinatorCapacity` (default `900`) and `-AnalystCapacity` (default `200`) set the
GPT-5.5 and GPT-5.4-mini deployment capacity in thousands of tokens per minute. Lower
them if the subscription has less quota in the target region.

If `.env` sets `AZURE_CONFIG_DIR`, every script uses that dedicated Azure CLI profile.
Live scripts also refuse to run if the active CLI subscription or tenant differs
from `.env`.

The resource group and every taggable Bicep resource receive base tags merged with
optional extra tags from `infra/tags.local.json`. This file and generated `.env` are
gitignored. No additional tag values are logged. Public networking remains enabled.
Provisioning initializes `.env` from `.env.example`, preserving existing settings.

`deploy_analyst.ps1` queues `az acr build --no-wait` for a Linux AMD64 image and polls
the run (live log streaming can crash the Windows CLI on Unicode output). Use
`register_analyst.py --use-version <n>` to finish RBAC, endpoint pinning and A2A for an
existing version. Registration creates a new toolbox version and immutable hosted-agent version, grants the agent identity
model/telemetry access, waits up to 15 minutes for active status, enables Responses
and incoming A2A, and pins endpoint traffic to that version. Web Search failure falls
back to a Code Interpreter toolbox; that fallback has no live web research. Agent
identity/RBAC propagation can take time. If ACR image pulls fail, verify the project
identity's AcrPull assignment; `azd ai agent` is the fallback deployment path (not
implemented here). Re-running registration creates new immutable versions.

`setup_coordinator.py` reuses a valid vector store (removing duplicate copies of
the same document) and creates a RemoteA2A connection plus two versions of the same
coordinator:

- **A2A / delegate version** (`COORDINATOR_A2A_VERSION`): the A2A tool only, with
  `send_credentials_for_agent_card=True` (Foundry agent cards require Entra auth).
  GPT-5.5 delegates reliably when A2A is its only tool; with File Search also present,
  it tended to keep searching instead.
- **Policy / orchestrated version** (`COORDINATOR_ORCHESTRATED_VERSION`): File Search
  only.

Both versions use `reasoning.effort=low`. The coordinator endpoint is pinned to the
delegate version. The backend and smoke test call:

```text
A2A mode:          delegate version (tool_choice=required)  ->  policy version [PHASE: FINAL]
Orchestrated mode: policy version [PHASE: BRIEF]  ->  hosted analyst  ->  policy version [PHASE: FINAL]
```

Callers using the project-level Responses client must pass the version in
`extra_body.agent_reference`. `smoke_test.py` runs the hosted analyst, then the
delegate step (it must observe an A2A call), then the policy step (it must observe
File Search). It fails rather than silently switching modes.

`setup_evaluations.py` sets up quality evaluation for both agents. Each agent is scored on task adherence, intent resolution and coherence, with `EVAL_MODEL_DEPLOYMENT` (default `gpt-54-mini`) as the judge.

- **Coordinator (prompt agent):** the script creates the continuous evaluation rule `caldova-coordinator-continuous-eval`. It fires on `responseCompleted` events, samples `EVAL_SAMPLING_RATE` percent of responses (default 100) and runs at most `EVAL_MAX_HOURLY_RUNS` evaluations per hour (default 30). Leave the sampling rate explicit: the service stores `null` when it is omitted. The rule covers both Coordinator versions. Scores appear on the agent's **Monitor** tab and as `gen_ai.evaluation.result` events in Application Insights. They are processed in batches, from about 15 minutes to a few hours after a response. Continuous evaluation runs as the project managed identity, which needs **Foundry User** on the account and **Reader** on Application Insights; the Bicep template grants both. The delegate version typically fails task adherence, because its summary goes beyond the "don't recommend yet" instruction; the policy version typically passes.
- **Analyst (hosted agent):** the service rejects evaluation rules for hosted agents ("agent kind 'hosted' … not supported for evaluation rules"). Instead, the script uploads the `caldova-analyst-scenarios` dataset for an **agent evaluation run**, which you start in the portal.
- **`--run-analyst-eval`:** starts that run from the SDK instead. On-demand evaluation runs, including agent-target runs and evaluations by response ID, are executed on the caller's behalf. In this repo's test tenant they failed with "Getting user token was not successful" when the caller's token came from the Azure CLI, so starting them from the portal is the reliable path.

Re-running the script is safe: it reuses the saved evaluation and dataset IDs from `.env`.

To delete the demo:

```powershell
.\scripts\teardown.ps1
# Or, without the confirmation prompt:
.\scripts\teardown.ps1 -Force
```

Teardown deletes the two agents best-effort, then requests asynchronous resource-group
deletion. It retains `.env`; provisioning refreshes resource outputs on redeployment.
Do not reuse old vector-store or agent-version IDs after deleting the resource group.
