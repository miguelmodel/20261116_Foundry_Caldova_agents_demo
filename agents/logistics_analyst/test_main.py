"""Offline graph, optional-Toolbox and telemetry checks; no Azure requests."""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from agents.logistics_analyst import main as analyst


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


FINAL_TEXT = """**Situation**
Fictitious Atlas disruption.
**Data checked**
SH-2041.
**Options**
| Option | Stockout days at flagships | Cost EUR | Notes |
|---|---|---|---|
| Wait | 13/13/14/14 | 0 | Simulation |
**Recommended option**
Seek human approval.
**Assumptions**
Fixed 2026-11-03 snapshot."""


class AgentGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_erp_execution_and_authoritative_current_turn_footer(self):
        model = ScriptedModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "get_shipment_status", "args": {"shipment_id": "SH-2041"}, "id": "erp-1"}]),
            AIMessage(content=FINAL_TEXT),
        ])
        graph = analyst.build_graph(model)
        result = await graph.ainvoke({"messages": [
            HumanMessage(content="Old question"),
            AIMessage(content="", tool_calls=[{"name": "get_demand_forecast", "args": {"sku": "CV-LIN-0042"}, "id": "old-1"}]),
            ToolMessage(content="Historical result", name="get_demand_forecast", tool_call_id="old-1", additional_kwargs={"caldova_tool_executed": True}),
            AIMessage(content="Old answer"),
            HumanMessage(content="Check SH-2041"),
        ]})
        self.assertEqual(result["messages"][-2].content, FINAL_TEXT)
        self.assertEqual(result["messages"][-1].content, "\n\nAnalyst tools used: get_shipment_status")
        self.assertTrue(any(isinstance(message, ToolMessage) and '"quantity_units": 12000' in message.content for message in result["messages"]))

    async def test_external_budget_handles_parallel_calls_and_streams_footer(self):
        executions = []

        @tool
        async def external_context(query: str) -> str:
            """Return mock external context without accessing the network."""
            executions.append(query)
            return "External context only"

        model = ScriptedModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "external_context", "args": {"query": str(i)}, "id": f"external-{i}"} for i in range(3)]),
            AIMessage(content=FINAL_TEXT),
        ])
        graph = analyst.build_graph(model, [external_context])
        messages = []
        async for message, metadata in graph.astream({"messages": [HumanMessage(content="Check external risk")]}, stream_mode="messages"):
            messages.append(message)
        self.assertEqual(len(executions), 2)
        self.assertTrue(any("budget exhausted" in message.content for message in messages if isinstance(message, ToolMessage)))
        self.assertEqual(messages[-1].content, "\n\nAnalyst tools used: external_context")

    async def test_external_timeout_is_a_nonfatal_result(self):
        @tool
        async def slow_context(query: str) -> str:
            """Wait without accessing any external system."""
            await asyncio.sleep(1)
            return "unreachable"

        model = ScriptedModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "slow_context", "args": {"query": "port"}, "id": "slow-1"}]),
            AIMessage(content=FINAL_TEXT),
        ])
        with patch.object(analyst, "TOOL_TIMEOUT_SECONDS", 0.01):
            result = await analyst.build_graph(model, [slow_context]).ainvoke({"messages": [HumanMessage(content="Check risk")]})
        self.assertTrue(any(isinstance(message, ToolMessage) and "TimeoutError" in message.content for message in result["messages"]))
        self.assertEqual(result["messages"][-1].content, "\n\nAnalyst tools used: slow_context")

    async def test_model_loop_stops_at_ten_calls(self):
        model = ScriptedModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "list_delayed_shipments", "args": {}, "id": f"loop-{i}"}])
            for i in range(12)
        ])
        result = await analyst.build_graph(model).ainvoke({"messages": [HumanMessage(content="Check shipments")]})
        self.assertEqual(model.i, 10)
        self.assertIn("limit", result["messages"][-2].content.lower())
        self.assertEqual(result["messages"][-1].content, "\n\nAnalyst tools used: list_delayed_shipments")

    async def test_toolbox_failure_does_not_prevent_erp_only_graph(self):
        with patch.dict(os.environ, {"TOOLBOX_NAME": "unavailable-demo-toolbox"}):
            with patch("langchain_azure_ai.tools.AzureAIProjectToolbox") as toolbox:
                toolbox.return_value.get_tools = AsyncMock(side_effect=RuntimeError("offline"))
                tools = await analyst.load_toolbox_tools("https://example.services.ai.azure.com/api/projects/x", object())
                self.assertEqual(tools, [])
                toolbox.return_value.get_tools.assert_awaited_once()
        graph = analyst.build_graph(ScriptedModel(responses=[AIMessage(content=FINAL_TEXT)]), tools)
        result = await graph.ainvoke({"messages": [HumanMessage(content="Hello")]})
        self.assertEqual(result["messages"][-1].content, "\n\nAnalyst tools used: none")

    async def test_gen_ai_agent_model_and_tool_spans_are_emitted(self):
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        model = ScriptedModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "list_delayed_shipments", "args": {}, "id": "trace-1"}]),
            AIMessage(content=FINAL_TEXT),
        ])
        graph = analyst.attach_tracing(analyst.build_graph(model), "openai")
        await graph.ainvoke({"messages": [HumanMessage(content="Check delayed shipments")]})
        operations = {span.attributes.get("gen_ai.operation.name") for span in exporter.get_finished_spans()}
        self.assertIn("invoke_agent", operations)
        self.assertIn("chat", operations)
        self.assertIn("execute_tool", operations)
        self.assertEqual(graph.config["recursion_limit"], analyst.RECURSION_LIMIT)
        provider.shutdown()


if __name__ == "__main__":
    unittest.main()
