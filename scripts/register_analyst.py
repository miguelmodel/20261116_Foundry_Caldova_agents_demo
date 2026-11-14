"""Register the cloud-built analyst image, its toolbox, identity, and pinned endpoint."""

from __future__ import annotations

import argparse
import sys
import time

from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import (
    AgentCard, AgentCardSkill, AutoCodeInterpreterToolParam, CodeInterpreterToolboxTool,
    ContainerConfiguration, HostedAgentDefinition, ProtocolVersionRecord, WebSearchToolboxTool,
)

from common import (
    COGNITIVE_USER_ROLE, FOUNDRY_USER_ROLE, METRICS_PUBLISHER_ROLE, agent_principal,
    assign_role, config, ensure_subscription_enabled, make_project, pin_endpoint, require, update_env,
)


def create_toolbox(project, name: str):
    try:
        toolbox = project.toolboxes.create_version(
            name=name,
            tools=[WebSearchToolboxTool(external_web_access=True)],
            description="Live disruption research for the Caldova Logistics Analyst.",
        )
        tool_type = "web_search"
    except HttpResponseError as exc:
        print(f"Web Search toolbox unavailable ({exc.status_code}); trying Code Interpreter.", flush=True)
        toolbox = project.toolboxes.create_version(
            name=name,
            tools=[CodeInterpreterToolboxTool(container=AutoCodeInterpreterToolParam(type="auto"))],
            description="Quantitative analysis fallback; live web research is unavailable.",
        )
        tool_type = "code_interpreter"
    print(f"Toolbox {name} version {toolbox.version}: {tool_type}", flush=True)
    update_env({"ANALYST_TOOLBOX_VERSION": toolbox.version, "ANALYST_TOOLBOX_TOOL": tool_type})
    return toolbox


def wait_active(project, name: str, version: str, timeout: int):
    deadline = time.monotonic() + timeout
    while True:
        details = project.agents.get_version(agent_name=name, agent_version=version)
        raw_status = details.status or "unknown"
        status = str(getattr(raw_status, "value", raw_status)).lower()
        print(f"{name}:{version} status={status}", flush=True)
        if status == "active":
            return details
        if status in {"failed", "deleting", "deleted"}:
            raise RuntimeError(
                f"Hosted agent {name}:{version} is {status}. Inspect hosted-agent logs in Foundry. "
                "For image-pull failures, verify project AcrPull; azd ai agent is the fallback."
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Hosted agent did not become active within {timeout}s. Inspect Foundry logs. "
                "For image-pull failures, azd ai agent is the fallback."
            )
        time.sleep(min(15, max(0, deadline - time.monotonic())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Fully qualified ACR image; defaults to ANALYST_CONTAINER_IMAGE.")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--use-version", help="Finish setup (RBAC, endpoint, A2A) for an existing agent version.")
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    values = config()
    require(values, "AZURE_SUBSCRIPTION_ID", "FOUNDRY_ACCOUNT_ARM_ID", "APPINSIGHTS_RESOURCE_ID",
            "FOUNDRY_PROJECT_ENDPOINT", "ANALYST_MODEL_PROVIDER", "ANALYST_MODEL_DEPLOYMENT")
    image = args.image or values.get("ANALYST_CONTAINER_IMAGE")
    if not image and not args.use_version:
        parser.error("--image or ANALYST_CONTAINER_IMAGE is required; run deploy_analyst.ps1")
    if values["ANALYST_MODEL_PROVIDER"] not in {"anthropic", "openai"}:
        raise ValueError("ANALYST_MODEL_PROVIDER must be anthropic or openai.")
    ensure_subscription_enabled(values)
    name = values["ANALYST_AGENT_NAME"]
    toolbox_name = values["ANALYST_TOOLBOX_NAME"]
    with DefaultAzureCredential(process_timeout=60) as credential, make_project(values, credential) as project:
        if args.use_version:
            version = str(args.use_version)
        else:
            create_toolbox(project, toolbox_name)
            created = project.agents.create_version(
                agent_name=name,
                definition=HostedAgentDefinition(
                    container_configuration=ContainerConfiguration(image=image),
                    protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
                    cpu="1", memory="2Gi",
                    environment_variables={
                        "MODEL_PROVIDER": values["ANALYST_MODEL_PROVIDER"],
                        "MODEL_DEPLOYMENT_NAME": values["ANALYST_MODEL_DEPLOYMENT"],
                        "TOOLBOX_NAME": toolbox_name,
                        "PYTHONNOUSERSITE": "1",
                        "AZURE_AI_PROJECT_ENDPOINT": values["FOUNDRY_PROJECT_ENDPOINT"],
                    },
                ),
            )
            version = str(created.version)
        # Grant access as soon as the identity exists so container startup can use its model.
        principal = agent_principal(project, name, version)
        update_env({
            "ANALYST_CANDIDATE_VERSION": version, "ANALYST_AGENT_PRINCIPAL_ID": principal,
            **({"ANALYST_CONTAINER_IMAGE": image} if image else {}),
        })
        for role, scope in (
            (COGNITIVE_USER_ROLE, values["FOUNDRY_ACCOUNT_ARM_ID"]),
            (FOUNDRY_USER_ROLE, values["FOUNDRY_ACCOUNT_ARM_ID"]),
            (METRICS_PUBLISHER_ROLE, values["APPINSIGHTS_RESOURCE_ID"]),
        ):
            assign_role(principal, role, scope, values["AZURE_SUBSCRIPTION_ID"])
        wait_active(project, name, version, args.timeout_seconds)
        pin_endpoint(
            project, name, version, incoming_a2a=True,
            card=AgentCard(
                version="1.0", description="Caldova Logistics Analyst: supply-chain recovery options and quantified trade-offs.",
                skills=[AgentCardSkill(
                    id="disruption-analysis", name="Supply-chain disruption analysis",
                    description="Analyze shipment and SKU delays against launch dates; compare recovery cost, timing, risk, and supplier claims.",
                    tags=["logistics", "supply-chain", "quantitative-analysis"],
                    examples=["Compare air freight and delayed ocean delivery for a launch-critical shipment."],
                )],
            ),
        )
        update_env({"ANALYST_AGENT_VERSION": version, "ANALYST_AGENT_PRINCIPAL_ID": principal})
        print(f"Ready: {name}:{version}; endpoint pinned; Responses + incoming A2A configured.")
        print("Validate hosted incoming A2A with smoke_test.py; if unsupported by this service rollout, use the backend orchestrated mode.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Analyst registration failed: {exc}", file=sys.stderr)
        sys.exit(1)
