"""Offline SDK contract check; constructs models but never authenticates or sends requests."""

from __future__ import annotations

import importlib.metadata
import inspect

from azure.ai.projects import AIProjectClient, models
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
from openai import NotFoundError, OpenAI, Stream


def show(name, value) -> None:
    print(f"{name}{inspect.signature(value)}")


def check_model(cls, **kwargs):
    show(cls.__name__, cls)
    model = cls(**kwargs)
    declared = cls._attr_to_rest_field
    unknown = kwargs.keys() - declared.keys()
    if unknown:
        raise AssertionError(f"{cls.__name__}: unknown fields {unknown}")
    print(f"  verified fields: {', '.join(kwargs) or '(empty configuration)'}")
    wire = model.as_dict()
    if any(key not in wire for key in kwargs):
        raise AssertionError(f"{cls.__name__}: fields lost in serialization")
    return model


def main() -> None:
    print(f"azure-ai-projects=={importlib.metadata.version('azure-ai-projects')}")
    if importlib.metadata.version("azure-ai-projects") != "2.10.0":
        raise RuntimeError("Run this demo with the pinned azure-ai-projects==2.10.0.")
    for cls in (AIProjectClient, DefaultAzureCredential, HttpResponseError, NotFoundError, OpenAI):
        show(cls.__name__, cls)
    show("DefaultAzureCredential.get_token", DefaultAzureCredential.get_token)
    show("AIProjectClient.get_openai_client", AIProjectClient.get_openai_client)
    # An inert credential is safe: client construction and model serialization do not call it.
    with AIProjectClient(
        endpoint="https://offline.invalid/api/projects/offline", credential=object(), allow_preview=True,
    ) as project:
        for group, methods in {
            "agents": ("create_version", "get_version", "get", "update_details", "delete"),
            "toolboxes": ("create_version",),
        }.items():
            for name in methods:
                show(f"{group}.{name}", getattr(getattr(project, group), name))
        with project.get_openai_client() as client:
            assert str(client.base_url).endswith("/api/projects/offline/openai/v1/")
        with project.get_openai_client(agent_name="analyst") as client:
            assert str(client.base_url).endswith("/agents/analyst/endpoint/protocols/openai/")
        print("  verified project-level and hosted-agent OpenAI endpoint paths (no requests)")
    container = check_model(models.ContainerConfiguration, image="registry.invalid/analyst:test")
    protocol = check_model(models.ProtocolVersionRecord, protocol="responses", version="2.0.0")
    hosted = check_model(
        models.HostedAgentDefinition, container_configuration=container, protocol_versions=[protocol],
        cpu="1", memory="2Gi", environment_variables={
            "MODEL_PROVIDER": "openai", "MODEL_DEPLOYMENT_NAME": "gpt-54-mini",
            "TOOLBOX_NAME": "caldova-analyst-tools", "PYTHONNOUSERSITE": "1",
        },
    )
    web = check_model(models.WebSearchToolboxTool, external_web_access=True)
    auto = check_model(models.AutoCodeInterpreterToolParam, type="auto")
    code = check_model(models.CodeInterpreterToolboxTool, container=auto)
    rule = check_model(models.FixedRatioVersionSelectionRule, agent_version="1", traffic_percentage=100)
    selector = check_model(models.VersionSelector, version_selection_rules=[rule])
    responses = check_model(models.ResponsesProtocolConfiguration)
    a2a_protocol = check_model(models.A2AProtocolConfiguration)
    protocols = check_model(models.ProtocolConfiguration, responses=responses, a2a=a2a_protocol)
    endpoint = check_model(models.AgentEndpointConfig, version_selector=selector, protocol_configuration=protocols)
    skill = check_model(
        models.AgentCardSkill, id="analysis", name="Analysis", description="Demo",
        tags=["logistics"], examples=["Compare recovery options."],
    )
    check_model(models.AgentCard, version="1.0", description="Demo", skills=[skill])
    file_search = check_model(models.FileSearchTool, vector_store_ids=["vs_offline"])
    a2a = check_model(
        models.A2ATool, a2a_version=models.A2AProtocolVersion.V1_0,
        project_connection_id="/subscriptions/offline/projects/offline/connections/analyst",
    )
    prompt = check_model(
        models.PromptAgentDefinition, model="gpt-55", instructions="Demo", tools=[file_search, a2a],
    )
    assert hosted.as_dict()["kind"] == "hosted"
    assert web.as_dict()["type"] == "web_search" and code.as_dict()["type"] == "code_interpreter"
    assert endpoint.as_dict()["protocol_configuration"] == {"responses": {}, "a2a": {}}
    assert prompt.as_dict()["tools"][1]["a2a_version"] == "1.0"
    for cls, fields in (
        (models.AgentVersionDetails, ("status", "version", "instance_identity")),
        (models.AgentDetails, ("instance_identity",)),
        (models.AgentIdentity, ("principal_id",)),
        (models.ToolboxVersionObject, ("version",)),
    ):
        show(cls.__name__, cls)
        cls()
        assert all(field in cls._attr_to_rest_field for field in fields)
        print(f"  verified response fields: {', '.join(fields)}")
    print("AgentVersionStatus:", ", ".join(member.value for member in models.AgentVersionStatus))
    with OpenAI(api_key="offline-not-a-real-key", base_url="https://offline.invalid") as client:
        for obj, name in (
            (client.vector_stores, "create"), (client.vector_stores, "retrieve"),
            (client.vector_stores.files, "list"), (client.vector_stores.files, "delete"),
            (client.vector_stores.files, "upload_and_poll"), (client.responses, "create"),
        ):
            show(f"OpenAI.{type(obj).__name__}.{name}", getattr(obj, name))
    show("OpenAI.Stream.__enter__", Stream.__enter__)
    print("PASS: all SDK names, keyword fields, and endpoint/tool wire payloads verified offline.")


if __name__ == "__main__":
    main()
