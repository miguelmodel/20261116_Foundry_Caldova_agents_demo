"""Focused helper tests. Fixtures stay in .deploy; every service call is mocked."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

from azure.core.exceptions import HttpResponseError
from openai import NotFoundError
import httpx

import common
import register_analyst
import setup_coordinator
import smoke_test


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.fixture = common.ROOT / ".deploy" / f"offline-tests-{uuid.uuid4().hex}"
        self.fixture.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.fixture)

    def test_env_updates_preserve_other_keys_and_roundtrip_special_values(self):
        path = self.fixture / "fixture.env"
        path.write_text("# keep this\nUNCHANGED=first\nVALUE=old\nVALUE=duplicate\n", encoding="utf-8")
        value = "C:\\Caldova\\O'Brien # launch €"
        common.update_env({"VALUE": value, "EMPTY": ""}, path)
        common.update_env({"NEW": "value with spaces"}, path)
        self.assertEqual(common.load_env(path), {
            "UNCHANGED": "first", "VALUE": value, "EMPTY": "", "NEW": "value with spaces",
        })
        self.assertIn("# keep this", path.read_text(encoding="utf-8"))
        self.assertEqual(path.read_text(encoding="utf-8").count("VALUE="), 1)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            common.update_env({"VALUE": "new\nINJECTED=yes"}, path)
        self.assertEqual(path.read_bytes(), before)

    def test_disabled_subscription_is_rejected(self):
        account = SimpleNamespace(stdout=json.dumps({"id": "offline", "tenantId": "t"}))
        state = SimpleNamespace(stdout=json.dumps({"state": "Disabled"}))
        with patch("common.az", side_effect=[account, state]) as cli:
            with self.assertRaisesRegex(RuntimeError, "No writes attempted"):
                common.ensure_subscription_enabled({"AZURE_SUBSCRIPTION_ID": "offline"})
            self.assertEqual(cli.call_count, 2)
            self.assertEqual(cli.call_args.args[0:3], ("rest", "--method", "get"))

    def test_wrong_cli_subscription_is_rejected(self):
        account = SimpleNamespace(stdout=json.dumps({"id": "other", "tenantId": "t"}))
        with patch("common.az", return_value=account):
            with self.assertRaisesRegex(RuntimeError, "AZURE_CONFIG_DIR"):
                common.ensure_subscription_enabled({"AZURE_SUBSCRIPTION_ID": "offline"})

    def test_existing_role_assignment_is_idempotent(self):
        with patch("common.az", return_value=SimpleNamespace(
            returncode=1, stderr="(RoleAssignmentExists) The role assignment already exists.",
        )):
            common.assign_role("principal", "role", "/scope", "subscription")

    def test_web_search_failure_uses_code_interpreter(self):
        project = MagicMock()
        project.toolboxes.create_version.side_effect = [
            HttpResponseError(message="Web Search unavailable"), SimpleNamespace(version="2"),
        ]
        with patch("register_analyst.update_env"), redirect_stdout(io.StringIO()):
            register_analyst.create_toolbox(project, "toolbox")
        calls = project.toolboxes.create_version.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["tools"][0].type, "web_search")
        self.assertEqual(calls[1].kwargs["tools"][0].as_dict()["container"], {"type": "auto"})

    def test_hosted_failed_status_stops_polling(self):
        project = MagicMock()
        project.agents.get_version.return_value = SimpleNamespace(status="failed")
        with redirect_stdout(io.StringIO()), patch("register_analyst.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "azd ai agent"):
                register_analyst.wait_active(project, "analyst", "1", 900)
            sleep.assert_not_called()

    def test_pinning_serializes_the_selected_version_and_both_protocols(self):
        project = MagicMock()
        common.pin_endpoint(project, "analyst", "7", incoming_a2a=True)
        payload = project.agents.update_details.call_args.kwargs
        self.assertEqual(payload["agent_name"], "analyst")
        endpoint = payload["agent_endpoint"].as_dict()
        self.assertEqual(endpoint["protocol_configuration"], {"responses": {}, "a2a": {}})
        self.assertEqual(endpoint["version_selector"]["version_selection_rules"], [
            {"type": "FixedRatio", "agent_version": "7", "traffic_percentage": 100},
        ])

    def test_vector_store_reuse_uploads_only_changed_pdf_and_detaches_old_revision(self):
        documents = self.fixture / "data" / "documents"
        documents.mkdir(parents=True)
        (documents / "unchanged.pdf").write_bytes(b"fixture-one")
        (documents / "missing.pdf").write_bytes(b"fixture-two")
        client = MagicMock()
        client.vector_stores.retrieve.return_value = SimpleNamespace(id="vs_existing", status="completed")
        client.vector_stores.files.list.return_value = [
            SimpleNamespace(id="file_one", status="completed"),
            SimpleNamespace(id="file_dup", status="completed"),
        ]
        client.files.retrieve.side_effect = lambda file_id: SimpleNamespace(filename="unchanged.pdf")
        client.vector_stores.files.upload_and_poll.return_value = SimpleNamespace(status="completed")
        with patch("setup_coordinator.ROOT", self.fixture), patch("setup_coordinator.update_env"), redirect_stdout(io.StringIO()):
            result = setup_coordinator.prepare_vector_store(client, {"VECTOR_STORE_ID": "vs_existing"})
        self.assertEqual(result, "vs_existing")
        client.vector_stores.create.assert_not_called()
        client.vector_stores.files.upload_and_poll.assert_called_once()
        client.vector_stores.files.delete.assert_called_once_with(file_id="file_dup", vector_store_id="vs_existing")

    def test_missing_vector_store_is_recreated_and_failed_indexing_is_rejected(self):
        documents = self.fixture / "data" / "documents"
        documents.mkdir(parents=True)
        (documents / "policy.pdf").write_bytes(b"fixture")
        client = MagicMock()
        client.vector_stores.retrieve.side_effect = NotFoundError(
            "gone", response=httpx.Response(404, request=httpx.Request("GET", "https://offline.invalid")), body=None,
        )
        client.vector_stores.create.return_value = SimpleNamespace(id="vs_new")
        client.vector_stores.files.list.return_value = []
        client.vector_stores.files.upload_and_poll.return_value = SimpleNamespace(
            status="failed", last_error="unsupported document",
        )
        with patch("setup_coordinator.ROOT", self.fixture), patch("setup_coordinator.update_env"), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Policy indexing failed"):
                setup_coordinator.prepare_vector_store(client, {"VECTOR_STORE_ID": "vs_gone"})
        client.vector_stores.files.delete.assert_not_called()

    def test_connection_payload_uses_agent_identity_and_prescribed_target(self):
        values = {
            "FOUNDRY_PROJECT_ARM_ID": "/subscriptions/offline/projects/project",
            "FOUNDRY_PROJECT_ENDPOINT": "https://offline.invalid/api/projects/project/",
            "A2A_CONNECTION_NAME": "analyst-a2a", "ANALYST_AGENT_NAME": "analyst",
        }
        credential = MagicMock()
        credential.get_token.return_value = SimpleNamespace(token="offline")
        with patch("setup_coordinator.httpx.put", return_value=SimpleNamespace(is_error=False)) as put, redirect_stdout(io.StringIO()):
            result = setup_coordinator.connect_analyst(credential, values)
        self.assertTrue(result.endswith("/connections/analyst-a2a"))
        properties = put.call_args.kwargs["json"]["properties"]
        self.assertEqual(properties["authType"], "AgenticIdentityToken")
        self.assertTrue(properties["target"].endswith("/agents/analyst/endpoint/protocols/a2a"))
        self.assertEqual(properties["audience"], "https://ai.azure.com")

    def test_agent_scope_fallback_is_limited_to_unsupported_scope(self):
        values = {"FOUNDRY_PROJECT_ARM_ID": "/project", "ANALYST_AGENT_NAME": "analyst", "AZURE_SUBSCRIPTION_ID": "sub"}
        with patch("setup_coordinator.assign_role", side_effect=[RuntimeError("InvalidScope"), None]) as assign, redirect_stdout(io.StringIO()):
            setup_coordinator.grant_analyst_access("principal", values)
        self.assertEqual(assign.call_args_list[1].args[2], "/project")
        with patch("setup_coordinator.assign_role", side_effect=RuntimeError("AuthorizationFailed")) as assign:
            with self.assertRaisesRegex(RuntimeError, "AuthorizationFailed"):
                setup_coordinator.grant_analyst_access("principal", values)
        self.assertEqual(assign.call_count, 1)

    def test_stream_requires_completion_and_tracks_real_tool_items_not_answer_text(self):
        response = SimpleNamespace(
            status="completed", error=None, output_text="a2a mentioned in the answer is not a call",
            output=[{"type": "message", "status": "completed"}],
        )
        client = MagicMock()
        client.responses.create.return_value.__enter__.return_value = iter([
            SimpleNamespace(type="response.output_item.done", item={"type": "a2a_call", "status": "completed"}),
            SimpleNamespace(type="response.output_item.done", item={"type": "file_search_call", "status": "completed"}),
            SimpleNamespace(type="response.completed", response=response),
        ])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(smoke_test.run_stream(client, "scenario"), {"a2a", "file_search"})
        client.responses.create.return_value.__enter__.return_value = iter([
            SimpleNamespace(type="response.completed", response=response),
        ])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(smoke_test.run_stream(client, "scenario"), set())
        client.responses.create.return_value.__enter__.return_value = iter([])
        with self.assertRaisesRegex(RuntimeError, "without response.completed"):
            smoke_test.run_stream(client, "scenario")

    def test_stream_rejects_failed_a2a_item(self):
        client = MagicMock()
        client.responses.create.return_value.__enter__.return_value = iter([
            SimpleNamespace(type="response.output_item.done", item={
                "type": "a2a_call", "status": "failed", "error": "HOSTED_AGENT_NOT_SUPPORTED",
            }),
        ])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "HOSTED_AGENT_NOT_SUPPORTED"):
            smoke_test.run_stream(client, "scenario")


if __name__ == "__main__":
    unittest.main()
