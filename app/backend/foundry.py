"""Real Foundry invocation and a defensive Responses-to-Control-Tower event bridge."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from opentelemetry.trace import Status, StatusCode

from .settings import Settings
from .telemetry import run_tracer

log = logging.getLogger(__name__)


def event(kind: str, **data) -> dict:
    return {"event": kind, "data": {**data, "ts": datetime.now(timezone.utc).isoformat()}}


def value(obj: Any, key: str, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def plain(obj: Any):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return obj


def compact(obj: Any, limit: int = 5500):
    try:
        text = json.dumps(plain(obj), default=str, ensure_ascii=False)
    except Exception:
        text = str(obj)
    return json.loads(text) if len(text) <= limit else {"preview": text[:limit], "truncated": True}


def output_text(obj: Any) -> str:
    """Read both typed Responses content and A2A's less predictable result envelopes."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple)):
        return "\n".join(filter(None, (output_text(part) for part in obj)))
    if obj is None:
        return ""
    for key in ("output_text", "text", "content", "output", "result", "response", "message", "parts", "artifacts"):
        found = value(obj, key)
        if found is not None and found is not obj:
            text = output_text(found)
            if text:
                return text
    return ""


def tool_kind(item: Any) -> str | None:
    item_type = str(value(item, "type", "")).lower()
    name = str(value(item, "name", "")).lower().replace("-", "_").replace(" ", "_")
    if "a2a" in item_type or "remote_agent" in item_type:
        return "a2a"
    if item_type == "mcp_call":
        return "web_search" if any(word in name for word in ("web_search", "websearch", "search_web", "bing")) else "mcp"
    known = {"file_search_call": "file_search", "web_search_call": "web_search", "function_call": "function", "code_interpreter_call": "code_interpreter"}
    return known.get(item_type) or ("function" if "_call" in item_type else None)


class ResponseFailure(RuntimeError):
    pass


class ResponseMapper:
    def __init__(self, agent: str):
        self.agent = agent
        self.text = ""
        self.response_id = None
        self.usage = {}
        self.completed = False
        self.calls: dict[str, dict] = {}
        self.finished_calls: set[str] = set()
        self.call_keys: dict[str, str] = {}
        self.a2a_text = ""

    def map(self, payload: Any) -> list[dict]:
        kind = str(value(payload, "type", "unknown"))
        events = [event("raw", agent=self.agent, type=kind, data=compact(payload))]
        if kind in {"response.failed", "response.incomplete", "error", "response.error"}:
            response = value(payload, "response", payload)
            error = value(response, "error") or value(response, "incomplete_details") or value(payload, "message") or kind
            raise ResponseFailure(json.dumps(compact(error, 1800), default=str))
        if kind == "response.output_text.delta":
            delta = str(value(payload, "delta", "") or "")
            self.text += delta
            events.append(event("text.delta", agent=self.agent, delta=delta))
        elif kind in {"response.output_item.added", "response.output_item.done"}:
            item = value(payload, "item", {})
            item_type = str(value(item, "type", "")).lower()
            if item_type.endswith("_call_output"):
                # e.g. a2a_preview_call_output: completes the call started earlier with the same call_id.
                key = self.call_keys.get(str(value(item, "call_id")))
                if kind.endswith(".done") and key and key not in self.finished_calls:
                    events.extend(self._complete(key, item, output_text(value(item, "output"))))
                return events
            tool_type = tool_kind(item)
            if tool_type:
                call_id = str(value(item, "id") or value(item, "call_id") or f"output-{value(payload, 'output_index', 0)}")
                name = str(value(item, "name") or value(item, "type", tool_type))
                if call_id not in self.calls:
                    self.calls[call_id] = {"agent": self.agent, "call_id": call_id, "tool_type": tool_type, "name": name}
                    if value(item, "call_id"):
                        self.call_keys[str(value(item, "call_id"))] = call_id
                    events.append(event("tool.started", **self.calls[call_id], arguments=compact(value(item, "arguments", value(item, "queries", {})), 2000)))
                    events.append(event("agent.status", agent=self.agent, status="calling_tool", detail=name))
                    if tool_type == "a2a":
                        events.extend([
                            event("handoff", **{"from": self.agent, "to": "analyst", "via": "a2a", "message_preview": output_text(value(item, "arguments"))[:240] or "Delegating logistics analysis"}),
                            event("agent.status", agent=self.agent, status="waiting", detail="Awaiting the Logistics Analyst via A2A"),
                            event("agent.status", agent="analyst", status="thinking", detail="Analyzing the disruption via A2A"),
                        ])
                result = output_text(value(item, "output") or value(item, "result") or value(item, "response"))
                # An A2A call without inline output completes when its *_call_output item arrives.
                if kind.endswith(".done") and call_id not in self.finished_calls and (tool_type != "a2a" or result):
                    events.extend(self._complete(call_id, item, result))
        elif kind == "response.completed":
            response = value(payload, "response", {})
            for pending in [key for key in self.calls if key not in self.finished_calls]:
                events.extend(self._complete(pending, {}, ""))
            self.completed = True
            self.response_id = value(response, "id")
            self.usage = plain(value(response, "usage", {})) or {}
            self.text = output_text(response) or self.text
            events.append(event("agent.message", agent=self.agent, text=self.text, response_id=self.response_id, usage=self.usage))
            events.append(event("agent.status", agent=self.agent, status="done", detail="Response complete"))
        return events

    def _complete(self, call_id: str, item: Any, result: str) -> list[dict]:
        self.finished_calls.add(call_id)
        call = self.calls[call_id]
        status = "error" if value(item, "status") in {"failed", "error", "cancelled"} or value(item, "error") else "completed"
        preview = result or json.dumps(compact(value(item, "results", value(item, "error", item)), 2200), default=str)
        events = [event("tool.completed", **call, status=status, output_preview=preview[:2400])]
        if call["tool_type"] == "a2a":
            if result:
                self.a2a_text = result
                events.append(event("agent.message", agent="analyst", text=result, response_id=value(item, "response_id"), usage=None))
            events.append(event("agent.status", agent="analyst", status="error" if status == "error" else "done", detail="A2A analysis returned" if status != "error" else "A2A delegation returned an error"))
        events.append(event("agent.status", agent=self.agent, status="thinking", detail="Considering the tool results"))
        return events


async def check_health(settings: Settings) -> dict:
    names = {"coordinator": settings.coordinator, "analyst": settings.analyst}
    if not settings.configured:
        return {"status": "error", "agents": {key: {"status": "error", "message": "Foundry is not configured. Run scripts/deploy_all.ps1 to create .env."} for key in names}}
    async with DefaultAzureCredential(process_timeout=30) as credential:
        async with AIProjectClient(endpoint=settings.endpoint, credential=credential, allow_preview=True, retry_total=1, connection_timeout=10, read_timeout=25) as project:
            async def check(key, name):
                try:
                    async with asyncio.timeout(40):
                        await project.agents.get(name)
                    return key, {"status": "ok", "message": f"{name} is reachable"}
                except Exception as exc:
                    return key, {"status": "error", "message": f"{type(exc).__name__}: {str(exc)[:700]}"}

            agents = dict(await asyncio.gather(*(check(key, name) for key, name in names.items())))
    return {"status": "ok" if all(item["status"] == "ok" for item in agents.values()) else "error", "agents": agents}


async def invoke(project: AIProjectClient, settings: Settings, agent: str, prompt: str, mode: str, emit: Callable) -> ResponseMapper:
    kwargs = {"timeout": httpx.Timeout(240, connect=15, write=30, pool=15), "max_retries": 0}
    client = project.get_openai_client(agent_name=settings.analyst, **kwargs) if agent == "analyst" else project.get_openai_client(**kwargs)
    request: dict = {"input": prompt, "stream": True}
    if agent == "coordinator":
        reference = {"type": "agent_reference", "name": settings.coordinator}
        # a2a -> delegate version (A2A tool only); orchestrated -> policy version (File Search).
        version = settings.a2a_version if mode == "a2a" else settings.orchestrated_version
        if version:
            reference["version"] = version
        request["extra_body"] = {"agent_reference": reference}
        # Bounded output keeps the per-request token reservation well inside the deployment's TPM.
        request["max_output_tokens"] = 8000
        if mode == "a2a":
            request["tool_choice"] = "required"
    mapper = ResponseMapper(agent)
    async with client:
        stream = await client.responses.create(**request)
        async with stream:
            async for payload in stream:
                try:
                    mapped = mapper.map(payload)
                except ResponseFailure:
                    await emit(event("raw", agent=agent, type=str(value(payload, "type", "error")), data=compact(payload)))
                    raise
                except Exception as exc:
                    log.warning("Unrecognized stream payload: %s", exc)
                    await emit(event("raw", agent=agent, type=str(value(payload, "type", "unknown")), data=compact(payload)))
                    continue
                for mapped_event in mapped:
                    await emit(mapped_event)
    if not mapper.completed:
        raise ResponseFailure(f"{agent} stream ended before response.completed.")
    return mapper


async def produce_run(settings: Settings, message: str, mode: str, scenario: str | None, emit: Callable):
    run_id = "run_" + uuid.uuid4().hex[:12]
    started = time.monotonic()
    active_agent = "coordinator"
    with run_tracer().start_as_current_span("caldova.control_tower.run", attributes={"run_id": run_id, "mode": mode, "scenario": scenario or "custom"}) as span:
        trace_id = f"{span.get_span_context().trace_id:032x}"
        await emit(event("run.started", run_id=run_id, trace_id=trace_id, mode=mode, agents={"coordinator": settings.coordinator, "analyst": settings.analyst}))
        await emit(event("agent.status", agent="analyst", status="idle", detail="Ready for delegation"))
        await emit(event("agent.status", agent="coordinator", status="thinking", detail="Preparing delegation brief" + (" for A2A" if mode == "a2a" else "")))
        responses = []
        try:
            if not settings.configured:
                raise ResponseFailure("Foundry is not configured. Run scripts/deploy_all.ps1, then restart the app. No simulated agent responses are used.")
            async with asyncio.timeout(600):
                async with DefaultAzureCredential(process_timeout=60) as credential:
                    async with AIProjectClient(endpoint=settings.endpoint, credential=credential, allow_preview=True, retry_total=0, connection_timeout=15, read_timeout=240) as project:
                        if mode == "a2a":
                            # Step 1: Coordinator delegates to the hosted Analyst through Foundry A2A (server-side).
                            delegated = await invoke(project, settings, "coordinator", f"Delegate this disruption to the Logistics Analyst via A2A:\n\n{message}", "a2a", emit)
                            responses.append(delegated)
                            analyst_report = delegated.a2a_text or delegated.text
                            # Step 2: same Coordinator agent (policy version) checks policies and decides.
                            await emit(event("agent.status", agent="coordinator", status="thinking", detail="Synthesizing final recommendation"))
                            final = await invoke(project, settings, "coordinator", f"[PHASE: FINAL]\nUse Caldova policies and the analyst report below (returned via A2A) to recommend actions, trade-offs, owners, and timing.\n\nOriginal request:\n{message}\n\nAnalyst report:\n{analyst_report}", "orchestrated", emit)
                            responses.append(final)
                        else:
                            brief = await invoke(project, settings, "coordinator", f"[PHASE: BRIEF]\nCreate a concise task brief for the Logistics Analyst. Do not delegate; the backend will relay it.\n\n{message}", mode, emit)
                            responses.append(brief)
                            await emit(event("handoff", **{"from": "coordinator", "to": "analyst", "via": "backend", "message_preview": brief.text[:240]}))
                            await emit(event("agent.status", agent="coordinator", status="waiting", detail="Awaiting the Logistics Analyst via backend"))
                            await emit(event("agent.status", agent="analyst", status="thinking", detail="Analyzing ERP data and external signals"))
                            active_agent = "analyst"
                            analyst = await invoke(project, settings, "analyst", f"Customer disruption:\n{message}\n\nCoordinator brief:\n{brief.text}", mode, emit)
                            responses.append(analyst)
                            await emit(event("handoff", **{"from": "analyst", "to": "coordinator", "via": "backend", "message_preview": analyst.text[:240]}))
                            active_agent = "coordinator"
                            await emit(event("agent.status", agent="coordinator", status="thinking", detail="Synthesizing final recommendation"))
                            final = await invoke(project, settings, "coordinator", f"[PHASE: FINAL]\nUse Caldova policies and the analyst report below to recommend actions, trade-offs, owners, and timing. Do not delegate again.\n\nOriginal request:\n{message}\n\nAnalyst report:\n{analyst.text}", mode, emit)
                            responses.append(final)
            usage = {key: sum(int(result.usage.get(key, 0) or 0) for result in responses) for key in ("input_tokens", "output_tokens", "total_tokens")}
            await emit(event("run.completed", run_id=run_id, trace_id=trace_id, final_text=final.text, duration_ms=round((time.monotonic() - started) * 1000), response_ids=[result.response_id for result in responses if result.response_id], usage=usage))
        except asyncio.CancelledError:
            span.set_attribute("cancelled", True)
            raise
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:500]))
            log.warning("Foundry run %s failed: %s", run_id, exc)
            await emit(event("agent.status", agent=active_agent, status="error", detail="Foundry request failed"))
            await emit(event("run.error", run_id=run_id, error=f"{type(exc).__name__}: {str(exc)[:2400]}"))
