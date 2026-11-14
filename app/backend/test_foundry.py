"""Focused SDK-shape and orchestration regression checks; no Azure calls."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from .foundry import ResponseFailure, ResponseMapper, invoke, output_text, produce_run
from .settings import Settings


class EventMappingTests(unittest.TestCase):
    def test_tool_families_and_unknown_calls(self):
        examples = [
            ("file_search_call", "", "file_search"),
            ("web_search_call", "", "web_search"),
            ("mcp_call", "web_search", "web_search"),
            ("mcp_call", "search_web", "web_search"),
            ("mcp_call", "Web Search", "web_search"),
            ("mcp_call", "inventory", "mcp"),
            ("function_call", "get_inventory_position", "function"),
            ("code_interpreter_call", "", "code_interpreter"),
            ("new_preview_call", "", "function"),
        ]
        for item_type, name, expected in examples:
            with self.subTest(item_type=item_type, name=name):
                mapper = ResponseMapper("analyst")
                item = {"type": item_type, "id": "tool-1", "name": name, "arguments": '{"sku":"CV-LIN-0042"}'}
                started = mapper.map(SimpleNamespace(type="response.output_item.added", item=item))
                completed = mapper.map({"type": "response.output_item.done", "item": {**item, "output": "result", "status": "completed"}})
                self.assertEqual(next(row["data"]["tool_type"] for row in started if row["event"] == "tool.started"), expected)
                self.assertEqual(next(row["data"]["output_preview"] for row in completed if row["event"] == "tool.completed"), "result")
                self.assertTrue(all("ts" in row["data"] for row in started + completed))
                self.assertFalse(any(row["event"] == "tool.completed" for row in mapper.map({"type": "response.output_item.done", "item": item})))

    def test_a2a_delegation_and_nested_report(self):
        for item_type in ("a2a_preview_call", "a2a_call", "remote_agent_call"):
            with self.subTest(item_type=item_type):
                mapper = ResponseMapper("coordinator")
                item = {"type": item_type, "id": "a2a-1", "arguments": "Investigate the disruption"}
                started = mapper.map({"type": "response.output_item.added", "item": item})
                self.assertEqual(next(row["data"]["via"] for row in started if row["event"] == "handoff"), "a2a")
                done = mapper.map({"type": "response.output_item.done", "item": {**item, "output": {"artifacts": [{"parts": [{"text": "Operational report"}]}]}}})
                report = next(row["data"] for row in done if row["event"] == "agent.message")
                self.assertEqual((report["agent"], report["text"]), ("analyst", "Operational report"))

    def test_a2a_call_and_separate_output_item_form_one_tool_card(self):
        mapper = ResponseMapper("coordinator")
        call = {"type": "a2a_preview_call", "id": "fc_1", "call_id": "call_1", "name": "caldova-analyst-a2a", "status": "completed"}
        events = mapper.map({"type": "response.output_item.added", "item": call})
        events += mapper.map({"type": "response.output_item.done", "item": call})
        self.assertFalse(any(row["event"] == "tool.completed" for row in events))
        output = {"type": "a2a_preview_call_output", "id": "fco_1", "call_id": "call_1", "output": "Analyst report"}
        events += mapper.map({"type": "response.output_item.added", "item": output})
        events += mapper.map({"type": "response.output_item.done", "item": output})
        self.assertEqual(sum(row["event"] == "tool.started" for row in events), 1)
        completed = [row["data"] for row in events if row["event"] == "tool.completed"]
        self.assertEqual([(row["call_id"], row["output_preview"]) for row in completed], [("fc_1", "Analyst report")])
        self.assertEqual(mapper.a2a_text, "Analyst report")

    def test_partial_text_completion_unknown_and_failed_events(self):
        mapper = ResponseMapper("coordinator")
        mapper.map({"type": "response.output_text.delta", "delta": "A first "})
        mapper.map({"type": "response.output_text.delta", "delta": "draft"})
        unknown = mapper.map({"type": "response.something.new", "unusual": [1, 2]})
        self.assertEqual(unknown[0]["event"], "raw")
        mapper.map({"type": "response.completed", "response": {"id": "resp_1", "output": [{"content": [{"text": "Final answer"}]}], "usage": {"total_tokens": 25}}})
        self.assertEqual((mapper.text, mapper.response_id, mapper.usage["total_tokens"]), ("Final answer", "resp_1", 25))
        for event_type in ("response.failed", "response.incomplete", "error"):
            with self.assertRaises(ResponseFailure):
                mapper.map({"type": event_type, "message": "unavailable"})

    def test_large_raw_payload_is_truncated(self):
        events = ResponseMapper("analyst").map({"type": "unknown", "output": "x" * 10000})
        self.assertTrue(events[0]["data"]["data"]["truncated"])
        self.assertLess(len(events[0]["data"]["data"]["preview"]), 6000)
        self.assertEqual(output_text({"result": [{"content": [{"text": "nested"}]}]}), "nested")


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_client_routing_and_version_reference(self):
        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def __aiter__(self):
                yield {"type": "response.completed", "response": {"id": "response-1", "output_text": "Done"}}

        settings = Settings(a2a_version="3", orchestrated_version="4")
        for agent, mode, version in (("coordinator", "a2a", "3"), ("coordinator", "orchestrated", "4"), ("analyst", "a2a", None)):
            with self.subTest(agent=agent, mode=mode):
                client = MagicMock()
                client.responses.create = AsyncMock(return_value=Stream())
                project = MagicMock()
                project.get_openai_client.return_value = client
                result = await invoke(project, settings, agent, "Disruption", mode, AsyncMock())
                request = client.responses.create.call_args.kwargs
                self.assertEqual(result.text, "Done")
                self.assertTrue(request["stream"])
                if version:
                    self.assertEqual(request["extra_body"]["agent_reference"], {"type": "agent_reference", "name": settings.coordinator, "version": version})
                    self.assertNotIn("agent_name", project.get_openai_client.call_args.kwargs)
                else:
                    self.assertNotIn("extra_body", request)
                    self.assertEqual(project.get_openai_client.call_args.kwargs["agent_name"], settings.analyst)

    async def test_unconfigured_run_has_trace_and_terminal_error(self):
        events = []
        await produce_run(Settings(endpoint=""), "test", "a2a", None, AsyncMock(side_effect=events.append))
        self.assertEqual(events[0]["event"], "run.started")
        self.assertRegex(events[0]["data"]["trace_id"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(events[0]["data"]["trace_id"], "0" * 32)
        self.assertEqual(events[-1]["event"], "run.error")

    async def test_orchestrated_order_and_usage(self):
        from . import foundry

        calls = []

        async def invoke(project, settings, agent, prompt, mode, emit):
            calls.append((agent, prompt))
            result = ResponseMapper(agent)
            result.text = f"result-{len(calls)}"
            result.response_id = f"response-{len(calls)}"
            result.usage = {"total_tokens": 10}
            return result

        events = []
        with patch.object(foundry, "invoke", side_effect=invoke):
            await produce_run(Settings(endpoint="https://example.services.ai.azure.com/api/projects/test"), "disruption", "orchestrated", "test", AsyncMock(side_effect=events.append))
        self.assertEqual([agent for agent, _ in calls], ["coordinator", "analyst", "coordinator"])
        self.assertIn("[PHASE: BRIEF]", calls[0][1])
        self.assertIn("result-1", calls[1][1])
        self.assertIn("[PHASE: FINAL]", calls[2][1])
        self.assertIn("result-2", calls[2][1])
        self.assertEqual(events[-1]["event"], "run.completed")
        self.assertEqual(events[-1]["data"]["usage"]["total_tokens"], 30)


if __name__ == "__main__":
    unittest.main()
