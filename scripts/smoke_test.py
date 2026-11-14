"""Live smoke test: hosted analyst Responses, then coordinator File Search + A2A."""

from __future__ import annotations

import sys

from azure.identity import DefaultAzureCredential

from common import config, ensure_subscription_enabled, make_project, require

SCENARIO = (
    "Use the supplied Caldova demo shipment data to evaluate the delayed shipment most at risk "
    "of missing its launch date. Identify the shipment, SKU, quantity, delay, and launch date. "
    "Compare feasible recovery options, quantify cost versus launch impact, and state assumptions. "
    "Recommend an action, policy approvals, and the supplier claim."
)


def item_dict(item) -> dict:
    if isinstance(item, dict):
        return item
    return item.model_dump(mode="json")


def inspect_item(item, calls: set[str]) -> None:
    payload = item_dict(item)
    item_type = str(payload.get("type", "unknown"))
    print(f"  output item: {item_type}", flush=True)
    lower = item_type.lower()
    if "a2a" in lower and "call" in lower:
        calls.add("a2a")
    if lower == "file_search_call":
        calls.add("file_search")
    if lower.endswith("_call_output"):
        return
    if payload.get("status") in {"failed", "incomplete", "cancelled"} or payload.get("error"):
        raise RuntimeError(f"Output item failed: {payload}")


def run_stream(client, prompt: str, *, extra_body: dict | None = None, **options) -> set[str]:
    calls = set()
    completed = False
    arguments = {"input": prompt, "stream": True, **options}
    if extra_body:
        arguments["extra_body"] = extra_body
    with client.responses.create(**arguments) as stream:
        for event in stream:
            event_type = event.type
            if event_type.endswith((".delta", "arguments.done", "content_part.added", "content_part.done")):
                continue
            print(f"event: {event_type}", flush=True)
            if "a2a" in event_type.lower() and "call" in event_type.lower():
                calls.add("a2a")
            if event_type in {"error", "response.failed", "response.incomplete"} or (
                "a2a" in event_type.lower() and event_type.endswith((".failed", ".error"))
            ):
                raise RuntimeError(f"Response failed: {event.model_dump(mode='json')}")
            if event_type in {"response.output_item.added", "response.output_item.done"}:
                inspect_item(event.item, calls)
            if event_type == "response.completed":
                response = event.response
                if response.status != "completed" or response.error:
                    raise RuntimeError(f"Response status={response.status}; error={response.error}")
                for item in response.output:
                    inspect_item(item, calls)
                if not response.output_text.strip():
                    raise RuntimeError("Completed response did not contain a textual answer.")
                print(f"\n{response.output_text}\n", flush=True)
                completed = True
    if not completed:
        raise RuntimeError("Stream ended without response.completed.")
    return calls


def main() -> None:
    values = config()
    require(values, "AZURE_SUBSCRIPTION_ID", "FOUNDRY_PROJECT_ENDPOINT", "ANALYST_AGENT_VERSION",
            "COORDINATOR_A2A_VERSION", "COORDINATOR_ORCHESTRATED_VERSION")
    ensure_subscription_enabled(values)

    def coordinator(version_key: str) -> dict:
        return {"agent_reference": {
            "type": "agent_reference", "name": values["COORDINATOR_AGENT_NAME"], "version": values[version_key],
        }}

    with DefaultAzureCredential(process_timeout=60) as credential, make_project(values, credential) as project:
        print(f"1/3 Hosted analyst {values['ANALYST_AGENT_NAME']} (pinned endpoint)...", flush=True)
        with project.get_openai_client(agent_name=values["ANALYST_AGENT_NAME"], timeout=900) as client:
            run_stream(client, SCENARIO)
        with project.get_openai_client(timeout=900) as client:
            print(f"2/3 Coordinator delegate version {values['COORDINATOR_A2A_VERSION']} (A2A)...", flush=True)
            calls = run_stream(
                client, "Delegate this disruption to the Logistics Analyst via A2A:\n\n" + SCENARIO,
                extra_body=coordinator("COORDINATOR_A2A_VERSION"), tool_choice="required", max_output_tokens=8000,
            )
            if "a2a" not in calls:
                raise RuntimeError("Coordinator completed without an observed A2A tool call.")
            print(f"3/3 Coordinator policy version {values['COORDINATOR_ORCHESTRATED_VERSION']} (File Search)...", flush=True)
            calls = run_stream(
                client, "[PHASE: BRIEF]\n" + SCENARIO,
                extra_body=coordinator("COORDINATOR_ORCHESTRATED_VERSION"), max_output_tokens=8000,
            )
            if "file_search" not in calls:
                raise RuntimeError("Coordinator policy step completed without an observed File Search call.")
        print("PASS: hosted analyst responded; coordinator delegated via A2A and used File Search.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        print(
            "Inspect Foundry logs and allow time for RBAC propagation. If hosted incoming A2A is "
            "unsupported in this rollout, the backend can use COORDINATOR_ORCHESTRATED_VERSION; "
            "this A2A smoke test deliberately remains failed.",
            file=sys.stderr,
        )
        sys.exit(1)
