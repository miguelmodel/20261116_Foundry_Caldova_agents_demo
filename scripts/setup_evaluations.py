"""Quality evaluation for both Caldova agents, scored by built-in Foundry evaluators (GPT-5.4-mini judge).

* Coordinator (prompt agent): a continuous evaluation rule scores completed responses
  automatically (task adherence, intent resolution, coherence). Scores appear on the agent's
  Monitor tab in the Foundry portal.
* Logistics Analyst (hosted agent): Foundry evaluation rules don't support hosted agents, so the
  script uploads the demo scenarios as a dataset for an agent evaluation. Start that evaluation in
  the portal (Build -> Evaluations -> Create, target = the Analyst agent), or pass
  --run-analyst-eval to start it from here. SDK-started runs need a user token that Foundry can
  exchange on your behalf; with some sign-in methods (for example, Azure CLI in some tenants) the
  run fails with "Getting user token was not successful", so the portal is the reliable route.

Re-running is safe: saved evaluations are reused and the rule is updated in place.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from azure.ai.projects.models import (
    AzureAIDataSourceConfig, ContinuousEvaluationRuleAction, EvaluationRule,
    EvaluationRuleEventType, EvaluationRuleFilter, TestingCriterionAzureAIEvaluator,
)
from azure.identity import DefaultAzureCredential
from openai.types.eval_create_params import DataSourceConfigCustom

from common import config, ensure_subscription_enabled, make_project, require, update_env

EVALUATORS = (
    ("Task adherence", "builtin.task_adherence", "{{sample.output_items}}"),
    ("Intent resolution", "builtin.intent_resolution", "{{sample.output_items}}"),
    ("Coherence", "builtin.coherence", "{{sample.output_text}}"),
)

ANALYST_DATASET = "caldova-analyst-scenarios"
ANALYST_QUERIES = (
    "Shipment SH-2041 (SKU CV-LIN-0042, 12,000 Linen Relaxed Shirts from Atlas Textiles) is delayed "
    "9 days. EU Spring launch is 2026-11-17. Compare recovery options and recommend one.",
    "Shipments SH-2043 and SH-2047 are stuck by congestion at Ningbo port. Assess the flagship "
    "stockout risk before the 2026-11-17 launch and recommend mitigation.",
    "Demand for the Satin Midi Skirt (SKU CV-SAT-0117) is up 300% after a social trend. Assess "
    "inventory cover at the flagships and recommend how to respond.",
)


def saved_eval(client, values: dict[str, str], env_key: str) -> str | None:
    if values.get(env_key):
        try:
            return client.evals.retrieve(values[env_key]).id
        except Exception:
            print(f"Saved evaluation {values[env_key]} not found; creating a new one.")
    return None


def setup_coordinator_rule(project, client, values: dict[str, str], judge: str, max_hourly_runs: int,
                           sampling_rate: float) -> None:
    agent = values["COORDINATOR_AGENT_NAME"]
    eval_id = saved_eval(client, values, "COORDINATOR_EVAL_ID")
    if not eval_id:
        eval_id = client.evals.create(
            name=f"Caldova continuous evaluation - {agent}",
            data_source_config=AzureAIDataSourceConfig(type="azure_ai_source", scenario="responses"),
            testing_criteria=[
                TestingCriterionAzureAIEvaluator(
                    type="azure_ai_evaluator", name=label, evaluator_name=evaluator,
                    initialization_parameters={"model": judge},
                )
                for label, evaluator, _ in EVALUATORS
            ],
        ).id
        update_env({"COORDINATOR_EVAL_ID": eval_id})
    rule = project.evaluation_rules.create_or_update(
        id=f"{agent}-continuous-eval",
        evaluation_rule=EvaluationRule(
            display_name=f"{agent} quality (continuous)",
            description="Task adherence, intent resolution and coherence on completed responses.",
            action=ContinuousEvaluationRuleAction(
                eval_id=eval_id, max_hourly_runs=max_hourly_runs, sampling_rate=sampling_rate,
            ),
            event_type=EvaluationRuleEventType.RESPONSE_COMPLETED,
            filter=EvaluationRuleFilter(agent_name=agent),
            enabled=True,
        ),
    )
    print(f"Continuous evaluation on for {agent}: rule {rule.id} ({sampling_rate:g}% of responses, "
          f"max {max_hourly_runs} runs/hour, judge {judge}).")


def ensure_analyst_dataset(project, values: dict[str, str]) -> str:
    if values.get("ANALYST_EVAL_DATASET_ID"):
        return values["ANALYST_EVAL_DATASET_ID"]
    path = Path(tempfile.gettempdir()) / f"{ANALYST_DATASET}.jsonl"
    path.write_text("".join(json.dumps({"query": q}) + "\n" for q in ANALYST_QUERIES), encoding="utf-8")
    dataset = project.datasets.upload_file(name=ANALYST_DATASET, version=str(int(time.time())), file_path=str(path))
    update_env({"ANALYST_EVAL_DATASET_ID": dataset.id})
    print(f"Uploaded dataset {ANALYST_DATASET} ({len(ANALYST_QUERIES)} scenarios) for the Analyst evaluation.")
    return dataset.id


def run_analyst_evaluation(client, values: dict[str, str], dataset_id: str, judge: str, wait_seconds: int) -> None:
    agent = values["ANALYST_AGENT_NAME"]
    eval_id = saved_eval(client, values, "ANALYST_EVAL_ID")
    if not eval_id:
        eval_id = client.evals.create(
            name=f"Caldova agent evaluation - {agent}",
            data_source_config=DataSourceConfigCustom(
                type="custom",
                item_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                include_sample_schema=True,
            ),
            testing_criteria=[
                TestingCriterionAzureAIEvaluator(
                    type="azure_ai_evaluator", name=label, evaluator_name=evaluator,
                    initialization_parameters={"model": judge},
                    data_mapping={"query": "{{item.query}}", "response": response},
                )
                for label, evaluator, response in EVALUATORS
            ],
        ).id
        update_env({"ANALYST_EVAL_ID": eval_id})
    run = client.evals.runs.create(
        eval_id=eval_id,
        name=f"{agent} scenarios {time.strftime('%Y-%m-%d %H:%M')}",
        data_source={
            "type": "azure_ai_target_completions",
            "source": {"type": "file_id", "id": dataset_id},
            "input_messages": {
                "type": "template",
                "template": [{"type": "message", "role": "user",
                              "content": {"type": "input_text", "text": "{{item.query}}"}}],
            },
            "target": {"type": "azure_ai_agent", "name": agent},
        },
    )
    print(f"Agent evaluation started for {agent}: run {run.id}.")
    deadline = time.monotonic() + wait_seconds
    while run.status not in {"completed", "failed", "canceled"} and time.monotonic() < deadline:
        time.sleep(15)
        run = client.evals.runs.retrieve(run_id=run.id, eval_id=eval_id)
        print(f"  status: {run.status}", flush=True)
    if run.status == "completed":
        for result in run.per_testing_criteria_results or []:
            print(f"  {result.testing_criteria}: passed {result.passed}, failed {result.failed}")
    elif run.status == "failed":
        raise RuntimeError(
            f"Agent evaluation run failed ({getattr(run, 'error', None)}). Start it from the Foundry portal "
            "instead: Build -> Evaluations -> Create, target = the Analyst agent, dataset = "
            f"{ANALYST_DATASET}."
        )
    if run.report_url:
        print(f"  Report: {run.report_url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-analyst-eval", action="store_true",
                        help="Also start the Analyst agent evaluation from this script.")
    parser.add_argument("--wait-seconds", type=int, default=900, help="How long to wait for the Analyst run.")
    args = parser.parse_args()
    values = config()
    require(values, "AZURE_SUBSCRIPTION_ID", "FOUNDRY_PROJECT_ENDPOINT")
    ensure_subscription_enabled(values)
    judge = values.get("EVAL_MODEL_DEPLOYMENT") or "gpt-54-mini"
    max_hourly_runs = int(values.get("EVAL_MAX_HOURLY_RUNS") or 30)
    sampling_rate = float(values.get("EVAL_SAMPLING_RATE") or 100)
    with DefaultAzureCredential(process_timeout=60) as credential, make_project(values, credential) as project:
        with project.get_openai_client() as client:
            setup_coordinator_rule(project, client, values, judge, max_hourly_runs, sampling_rate)
            dataset_id = ensure_analyst_dataset(project, values)
            if args.run_analyst_eval:
                run_analyst_evaluation(client, values, dataset_id, judge, args.wait_seconds)
            else:
                print(f"Analyst (hosted agent): in the Foundry portal, open Build -> Evaluations -> Create, choose "
                      f"the agent {values['ANALYST_AGENT_NAME']}, dataset {ANALYST_DATASET}, and the Task adherence, "
                      f"Intent resolution and Coherence evaluators with judge {judge}.")
    print("Results appear in the Foundry portal a few minutes after new traffic or runs complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Evaluation setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
