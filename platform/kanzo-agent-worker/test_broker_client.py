from __future__ import annotations

import unittest
from unittest.mock import patch

from broker_client import BrokerClient, BrokerClientError


class BrokerClientEndpointTests(unittest.TestCase):
    def test_internal_cluster_endpoint_is_allowed(self) -> None:
        client = BrokerClient("http://kanzo-sandbox-broker.mcp.svc.cluster.local:8080", "x" * 32)
        self.assertEqual(client.base_url, "http://kanzo-sandbox-broker.mcp.svc.cluster.local:8080")

    def test_tailnet_endpoint_requires_explicit_flag_and_https(self) -> None:
        with patch.dict("os.environ", {"KANZO_BROKER_ALLOW_TAILNET": "true"}):
            client = BrokerClient("https://kanzo-broker.tail5389e9.ts.net", "x" * 32)
            self.assertEqual(client.base_url, "https://kanzo-broker.tail5389e9.ts.net")
            with self.assertRaises(BrokerClientError):
                BrokerClient("http://kanzo-broker.tail5389e9.ts.net", "x" * 32)

    def test_tailnet_endpoint_is_rejected_without_explicit_flag(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(BrokerClientError):
                BrokerClient("https://kanzo-broker.tail5389e9.ts.net", "x" * 32)

    def test_untrusted_public_endpoint_is_rejected(self) -> None:
        with patch.dict("os.environ", {"KANZO_BROKER_ALLOW_TAILNET": "true"}):
            with self.assertRaises(BrokerClientError):
                BrokerClient("https://example.com", "x" * 32)

    def test_runner_heartbeat_sends_only_bounded_identity(self) -> None:
        client = BrokerClient("http://kanzo-sandbox-broker.mcp.svc:8080", "x" * 32)
        with patch.object(client, "_post_json", return_value={"runner_id": "k3s"}) as post:
            self.assertEqual(client.heartbeat_runner("k3s", "linux"), {"runner_id": "k3s"})
        post.assert_called_once_with(
            "/v1/runners/heartbeat", {"runner_id": "k3s", "runner_os": "linux"}, timeout=10
        )
        with self.assertRaises(BrokerClientError):
            client.heartbeat_runner("../bad", "linux")


if __name__ == "__main__":
    unittest.main()
