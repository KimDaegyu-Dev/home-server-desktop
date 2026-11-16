"""Tests for committed completion events from the resident Hermes Gateway."""

from __future__ import annotations

import unittest
import asyncio
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

from gateway_execution import _committed_completion_text, execute


class CommittedCompletionTests(unittest.TestCase):
    def test_accepts_matching_successful_completion(self) -> None:
        payload = {
            "admission_id": "admission-1",
            "outcome": "completed",
            "status": "complete",
            "text": "done",
        }
        self.assertEqual(_committed_completion_text(payload, "admission-1"), "done")

    def test_rejects_wrong_admission(self) -> None:
        payload = {
            "admission_id": "other",
            "outcome": "completed",
            "status": "complete",
            "text": "done",
        }
        self.assertIsNone(_committed_completion_text(payload, "admission-1"))

    def test_rejects_non_success_status_or_outcome(self) -> None:
        for field, value in (("status", "error"), ("outcome", "interrupted")):
            with self.subTest(field=field):
                payload = {
                    "admission_id": "admission-1",
                    "outcome": "completed",
                    "status": "complete",
                    "text": "not a success",
                }
                payload[field] = value
                self.assertIsNone(_committed_completion_text(payload, "admission-1"))


class GatewayExecutionCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_event_finishes_without_receipt_rpc(self) -> None:
        class Client:
            def __init__(self):
                self.events = asyncio.Queue()
                self.rpc_methods = []

            async def rpc(self, method, **kwargs):
                self.rpc_methods.append(method)
                if method == "session.resume":
                    return {"stored_session_id": "session-1", "session_id": "session-1", "pending": []}
                if method == "prompt.submit":
                    return {"admission_id": "admission-1"}
                raise AssertionError(f"unexpected RPC: {method}")

        class Connected:
            def __init__(self, client):
                self.client = client

            async def __aenter__(self):
                return self.client

            async def __aexit__(self, *_args):
                return False

        client = Client()
        await client.events.put({"params": {
            "session_id": "session-1",
            "type": "message.complete",
            "payload": {
                "admission_id": "admission-1",
                "outcome": "completed",
                "status": "complete",
                "text": "Verified result",
            },
        }})
        hermes_cli = types.ModuleType("hermes_cli")
        hermes_cli.__path__ = []
        gateway_client = types.ModuleType("hermes_cli.gateway_client")
        gateway_client.connect_gateway = lambda **_kwargs: Connected(client)
        gateway_runtime = types.ModuleType("hermes_cli.gateway_runtime")
        endpoint = types.SimpleNamespace(profile_id="")

        def discover(profile_home, timeout):
            self.assertEqual(timeout, 2.0)
            endpoint.profile_id = str(profile_home)
            return types.SimpleNamespace(endpoint=endpoint, state="ready")

        gateway_runtime.discover_gateway_endpoint = discover
        emitted = []

        with tempfile.TemporaryDirectory() as temp:
            profile_home = Path(temp) / "profile"
            profile_home.mkdir()
            prompt = Path(temp) / "prompt.txt"
            prompt.write_text("read-only test", encoding="utf-8")
            with patch.dict(sys.modules, {
                "hermes_cli": hermes_cli,
                "hermes_cli.gateway_client": gateway_client,
                "hermes_cli.gateway_runtime": gateway_runtime,
            }), patch("gateway_execution._prepare_imports"), patch("gateway_execution._emit", emitted.append):
                result = await execute(profile_home, "session-1", prompt)

        self.assertEqual(result, 0)
        self.assertEqual(client.rpc_methods, ["session.resume", "prompt.submit"])
        self.assertIn({"type": "text", "text": "Verified result"}, emitted)
        self.assertIn({"type": "executor_exit", "code": 0, "settled": True, "gateway_turn_settled": True}, emitted)

    def test_rejects_missing_or_empty_response(self) -> None:
        for text in (None, "", "   "):
            with self.subTest(text=text):
                payload = {
                    "admission_id": "admission-1",
                    "outcome": "completed",
                    "status": "complete",
                    "text": text,
                }
                self.assertIsNone(_committed_completion_text(payload, "admission-1"))


if __name__ == "__main__":
    unittest.main()
