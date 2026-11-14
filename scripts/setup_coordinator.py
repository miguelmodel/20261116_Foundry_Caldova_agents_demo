"""Upload policies, connect the analyst, and create both coordinator versions."""

from __future__ import annotations

import sys

import httpx
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import A2AProtocolVersion, A2ATool, FileSearchTool, PromptAgentDefinition, Reasoning
from openai import NotFoundError

from common import (
    AGENT_CONSUMER_ROLE, ROOT, agent_principal, assign_role, config, ensure_subscription_enabled,
    make_project, pin_endpoint, require, update_env,
)


def prepare_vector_store(client, values: dict[str, str]) -> str:
    pdfs = sorted((ROOT / "data" / "documents").glob("*.pdf"))
    if not pdfs:
        raise RuntimeError("No policy PDFs found in data\\documents. Generate the demo data first.")
    store = None
    if values.get("VECTOR_STORE_ID"):
        try:
            store = client.vector_stores.retrieve(values["VECTOR_STORE_ID"])
            if store.status == "expired":
                store = None
        except NotFoundError:
            print("Previous vector store no longer exists; creating a new one.")
    if store is None:
        store = client.vector_stores.create(name="caldova-policies")
        update_env({"VECTOR_STORE_ID": store.id})
    indexed = list(client.vector_stores.files.list(vector_store_id=store.id))
    by_name: dict[str, list] = {}
    for item in indexed:
        try:
            filename = client.files.retrieve(item.id).filename
        except Exception:
            filename = None
        by_name.setdefault(filename, []).append(item)
    for filename, items in by_name.items():
        # Drop duplicate copies of the same document (keeps the first completed one).
        for extra in items[1:]:
            client.vector_stores.files.delete(file_id=extra.id, vector_store_id=store.id)
            print(f"Removed duplicate indexed copy: {filename}")
    present = {name for name, items in by_name.items() if name and items[0].status == "completed"}
    for path in pdfs:
        if path.name in present:
            print(f"Policy already indexed: {path.name}")
            continue
        with path.open("rb") as document:
            uploaded = client.vector_stores.files.upload_and_poll(
                vector_store_id=store.id, file=document,
                poll_interval_ms=1000, max_wait_seconds=600,
            )
        if uploaded.status != "completed":
            raise RuntimeError(f"Policy indexing failed for {path.name}: {uploaded.status}; {uploaded.last_error}")
        print(f"Indexed policy: {path.name}")
    update_env({"VECTOR_STORE_ID": store.id})
    return store.id


def connect_analyst(credential, values: dict[str, str]) -> str:
    connection_id = (
        f"{values['FOUNDRY_PROJECT_ARM_ID'].rstrip('/')}/connections/{values['A2A_CONNECTION_NAME']}"
    )
    target = (
        f"{values['FOUNDRY_PROJECT_ENDPOINT'].rstrip('/')}"
        f"/agents/{values['ANALYST_AGENT_NAME']}/endpoint/protocols/a2a"
    )
    token = credential.get_token("https://management.azure.com/.default").token
    response = httpx.put(
        f"https://management.azure.com{connection_id}?api-version=2025-04-01-preview",
        headers={"Authorization": f"Bearer {token}"},
        json={"properties": {
            "authType": "AgenticIdentityToken", "category": "RemoteA2A", "target": target,
            "audience": "https://ai.azure.com", "credentials": {}, "metadata": {},
        }},
        timeout=120,
    )
    if response.is_error:
        raise RuntimeError(f"RemoteA2A connection failed ({response.status_code}): {response.text}")
    print(f"RemoteA2A connection ready: {values['A2A_CONNECTION_NAME']}")
    return connection_id


def grant_analyst_access(principal: str, values: dict[str, str]) -> None:
    project_scope = values["FOUNDRY_PROJECT_ARM_ID"].rstrip("/")
    agent_scope = f"{project_scope}/agents/{values['ANALYST_AGENT_NAME']}"
    try:
        assign_role(principal, AGENT_CONSUMER_ROLE, agent_scope, values["AZURE_SUBSCRIPTION_ID"])
    except RuntimeError as exc:
        if not any(marker in str(exc) for marker in (
            "ScopeNotFound", "ResourceNotFound", "InvalidScope", "InvalidResourceType",
            "UnsupportedResourceType", "RoleAssignmentScopeNotSupported",
        )):
            raise
        print("Agent-level RBAC scope unavailable; granting Foundry Agent Consumer at project scope.")
        assign_role(principal, AGENT_CONSUMER_ROLE, project_scope, values["AZURE_SUBSCRIPTION_ID"])


def main() -> None:
    values = config()
    require(values, "AZURE_SUBSCRIPTION_ID", "FOUNDRY_PROJECT_ARM_ID",
            "FOUNDRY_PROJECT_ENDPOINT", "ANALYST_AGENT_VERSION", "COORDINATOR_MODEL_DEPLOYMENT")
    ensure_subscription_enabled(values)
    name = values["COORDINATOR_AGENT_NAME"]
    instructions_dir = ROOT / "agents" / "coordinator"
    with DefaultAzureCredential(process_timeout=60) as credential, make_project(values, credential) as project:
        with project.get_openai_client() as client:
            store_id = prepare_vector_store(client, values)
        connection_id = connect_analyst(credential, values)
        # Delegate version: A2A tool only (GPT-5.5 reliably delegates when A2A is its sole tool).
        a2a = project.agents.create_version(
            agent_name=name,
            definition=PromptAgentDefinition(
                model=values["COORDINATOR_MODEL_DEPLOYMENT"],
                reasoning=Reasoning(effort="low"),
                instructions=(instructions_dir / "instructions.md").read_text(encoding="utf-8"),
                tools=[
                    A2ATool(
                        a2a_version=A2AProtocolVersion.V1_0, project_connection_id=connection_id,
                        # Foundry A2A targets require an authenticated agent-card fetch.
                        send_credentials_for_agent_card=True,
                    ),
                ],
            ),
        )
        # Persist partial progress so setup can be diagnosed if a later service call fails.
        update_env({"COORDINATOR_A2A_VERSION": str(a2a.version)})
        orchestrated = project.agents.create_version(
            agent_name=name,
            definition=PromptAgentDefinition(
                model=values["COORDINATOR_MODEL_DEPLOYMENT"],
                reasoning=Reasoning(effort="low"),
                instructions=(instructions_dir / "instructions_orchestrated.md").read_text(encoding="utf-8"),
                tools=[FileSearchTool(vector_store_ids=[store_id], max_num_results=6)],
            ),
        )
        update_env({"COORDINATOR_ORCHESTRATED_VERSION": str(orchestrated.version)})
        principals = {
            agent_principal(project, name, str(a2a.version)),
            agent_principal(project, name, str(orchestrated.version)),
        }
        for principal in principals:
            grant_analyst_access(principal, values)
        pin_endpoint(project, name, str(a2a.version))
        update_env({
            "COORDINATOR_AGENT_PRINCIPAL_ID": agent_principal(project, name, str(a2a.version)),
            "A2A_CONNECTION_ID": connection_id,
        })
        print(f"Ready: {name}; A2A={a2a.version}, orchestrated={orchestrated.version}; endpoint pinned to A2A.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Coordinator setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
