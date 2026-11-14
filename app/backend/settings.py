"""Environment-backed configuration, independent of Azure connectivity."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlencode

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
if os.getenv("AZURE_CONFIG_DIR"):
    # Per-project Azure CLI profile used by DefaultAzureCredential's `az` token calls.
    os.environ["AZURE_CONFIG_DIR"] = os.path.expandvars(os.environ["AZURE_CONFIG_DIR"])

SCENARIOS = [
    {
        "id": "supplier-delay",
        "title": "Supplier delay",
        "subtitle": "SH-2041 · Atlas Textiles",
        "tag": "01 / SUPPLY",
        "prompt": "Shipment SH-2041 from Atlas Textiles (12,000 Linen Relaxed Shirts, SKU CV-LIN-0042) is delayed 9 days. Our EU Spring launch is on 2026-11-17. What should we do?",
    },
    {
        "id": "port-congestion",
        "title": "Port congestion",
        "subtitle": "Ningbo · SH-2043 + SH-2047",
        "tag": "02 / LOGISTICS",
        "prompt": "Shipments SH-2043 and SH-2047 are stuck by congestion at Ningbo port. Assess the impact on our launch commitments and recommend mitigation options.",
    },
    {
        "id": "demand-spike",
        "title": "Viral demand spike",
        "subtitle": "CV-SAT-0117 · Satin Midi Skirt",
        "tag": "03 / DEMAND",
        "prompt": "Demand for the Satin Midi Skirt (SKU CV-SAT-0117) has increased 300% after a social trend. How should Caldova respond across inventory, suppliers, and allocation?",
    },
]


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip() or default


@dataclass(frozen=True)
class Settings:
    endpoint: str = field(default_factory=lambda: env("FOUNDRY_PROJECT_ENDPOINT"))
    coordinator: str = field(default_factory=lambda: env("COORDINATOR_AGENT_NAME", "caldova-coordinator"))
    analyst: str = field(default_factory=lambda: env("ANALYST_AGENT_NAME", "caldova-logistics-analyst"))
    a2a_version: str = field(default_factory=lambda: env("COORDINATOR_A2A_VERSION"))
    orchestrated_version: str = field(default_factory=lambda: env("COORDINATOR_ORCHESTRATED_VERSION"))
    analyst_version: str = field(default_factory=lambda: env("ANALYST_AGENT_VERSION"))
    coordinator_model: str = field(default_factory=lambda: env("COORDINATOR_MODEL_DEPLOYMENT", "gpt-55"))
    analyst_provider: str = field(default_factory=lambda: env("ANALYST_MODEL_PROVIDER", "anthropic"))
    analyst_model: str = field(default_factory=lambda: env("ANALYST_MODEL_DEPLOYMENT", "claude-opus-55"))

    @property
    def configured(self) -> bool:
        return self.endpoint.startswith("https://") and "<" not in self.endpoint

    def public_config(self) -> dict:
        tenant = env("AZURE_TENANT_ID")
        arm_id = env("FOUNDRY_PROJECT_ARM_ID")
        insights_id = env("APPINSIGHTS_RESOURCE_ID")
        subscription = env("AZURE_SUBSCRIPTION_ID")
        group = env("AZURE_RESOURCE_GROUP")
        portal = "https://ai.azure.com/"
        if arm_id:
            portal += "?" + urlencode({"wsid": arm_id, "tid": tenant})
        azure_portal = f"https://portal.azure.com/#@{quote(tenant, safe='')}"
        model = self.analyst_model
        if "claude" in model.lower() or self.analyst_provider.lower() == "anthropic":
            model = "Claude Opus 5.5" if "55" in model or "5.5" in model else model
        elif "54" in model or "5.4" in model:
            model = "GPT-5.4-mini"
        coordinator_model = "GPT-5.5" if self.coordinator_model in {"gpt-55", "gpt-5.5"} else self.coordinator_model
        return {
            "agents": {
                "coordinator": {
                    "name": self.coordinator,
                    "kind": "Prompt agent",
                    "model": coordinator_model,
                    "versions": {"a2a": self.a2a_version or "latest", "orchestrated": self.orchestrated_version or "latest"},
                    "tools": ["File Search", "A2A"],
                },
                "analyst": {
                    "name": self.analyst,
                    "kind": "Hosted agent · LangGraph",
                    "model": model,
                    "version": self.analyst_version or "latest",
                    "tools": ["ERP tools", "Web Search (Foundry Toolbox)"],
                },
            },
            "scenarios": SCENARIOS,
            "links": {
                "foundry_portal": portal,
                "foundry_traces_hint": f"Agents → {self.coordinator} → Traces",
                "app_insights": f"{azure_portal}/resource{insights_id}/overview" if insights_id else "",
                "resource_group": f"{azure_portal}/resource/subscriptions/{quote(subscription, safe='')}/resourceGroups/{quote(group, safe='')}/overview" if subscription and group else "",
            },
            "project_endpoint": self.endpoint,
            "configured": self.configured,
        }
