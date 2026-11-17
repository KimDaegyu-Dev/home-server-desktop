"""Tests for the broker's per-run filesystem boundary."""

from __future__ import annotations

import tempfile
import json
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import patch

import sandbox_broker


class SandboxPodMountTests(unittest.TestCase):
    def _pod_for(self, read_only: bool) -> dict:
        api = sandbox_broker.KubernetesAPI.__new__(sandbox_broker.KubernetesAPI)
        api.namespace = "kanzo-sandboxes"
        captured = {}

        def request(method, path, payload=None):
            captured.update(method=method, path=path, payload=payload)
            return 201, {}

        api.request = request
        with patch.dict("os.environ", {"KANZO_SANDBOX_IMAGE": "localhost/test-sandbox:unit"}):
            api.create_run_pod(
                pod_name="run-unit",
                runner_token="token",
                source_url="http://source.invalid/archive",
                source_token="source-token",
                source_sha256="a" * 64,
                source_git_sha="c" * 40,
                read_only=read_only,
            )
        self.assertEqual((captured["method"], captured["path"]), (
            "POST", "/api/v1/namespaces/kanzo-sandboxes/pods",
        ))
        return captured["payload"]

    def test_read_only_mode_mounts_workspace_read_only(self) -> None:
        pod = self._pod_for(True)
        runner = pod["spec"]["containers"][0]
        mounts = {mount["mountPath"]: mount for mount in runner["volumeMounts"]}

        self.assertTrue(mounts["/workspace"]["readOnly"])
        self.assertTrue(runner["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertNotIn("home", {volume["name"] for volume in pod["spec"]["volumes"]})
        env = {item["name"]: item["value"] for item in runner["env"]}
        self.assertEqual(env["KANZO_SOURCE_GIT_SHA"], "c" * 40)

    def test_writable_mode_keeps_workspace_writable(self) -> None:
        pod = self._pod_for(False)
        runner = pod["spec"]["containers"][0]
        mounts = {mount["mountPath"]: mount for mount in runner["volumeMounts"]}

        self.assertFalse(mounts["/workspace"]["readOnly"])
        self.assertTrue(runner["securityContext"]["readOnlyRootFilesystem"])


class BrokerChildSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        with patch("sandbox_broker.KubernetesAPI"):
            self.store = sandbox_broker.BrokerStore(root, root / "broker.sqlite3", b"k" * 32)
        self.task_id = "project#7"
        self.run_id = "a" * 12
        self.root_session = "hermes-root"
        nonce = "run-nonce"
        self.lease = self.store._derive_lease(self.task_id, self.run_id, nonce)
        now = sandbox_broker._now()
        with self.store.session() as db:
            db.execute("""INSERT INTO runs(task_id,run_id,archive_sha256,source_git_sha,read_only,
                lease_hash,state,archive_name,source_token_hash,lease_nonce,hermes_session_id,profile,
                created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (self.task_id, self.run_id, "b" * 64, "c" * 40, 1,
                        sandbox_broker._digest(self.lease), "bound", "archive.tar.gz", "d" * 64,
                        nonce, self.root_session, "researcher", now, now, now + 3600))

    def test_delegate_tree_resolves_to_parent_run_and_revokes_child(self) -> None:
        self.assertEqual(
            self.store.register_child_session(self.root_session, "child-one")["state"], "bound"
        )
        self.assertTrue(self.store.register_child_session(self.root_session, "child-one")["idempotent"])
        self.store.register_child_session("child-one", "child-two")

        resolved = self.store.authorize_session("child-two")
        self.assertEqual((resolved["task_id"], resolved["run_id"]), (self.task_id, self.run_id))
        handle = "environment-child-two"
        now = sandbox_broker._now()
        with self.store.session() as db:
            db.execute("INSERT INTO environments(handle,task_id,run_id,hermes_session_id,created_at) "
                       "VALUES(?,?,?,?,?)", (handle, self.task_id, self.run_id, "child-two", now))
        run = self.store.authorize({
            "task_id": self.task_id, "run_id": self.run_id, "hermes_session_id": "child-two",
            "lease": self.lease, "environment_handle": handle,
        })
        self.assertEqual(run["hermes_session_id"], self.root_session)

        self.store.close_child_session("child-one", "child-two")
        with self.assertRaises(sandbox_broker.BrokerError):
            self.store.authorize_session("child-two")
        with self.assertRaises(sandbox_broker.BrokerError):
            self.store.authorize({
                "task_id": self.task_id, "run_id": self.run_id, "hermes_session_id": "child-two",
                "lease": self.lease, "environment_handle": handle,
            })

    def test_child_cannot_be_registered_under_an_unbound_parent(self) -> None:
        with self.assertRaises(sandbox_broker.BrokerError):
            self.store.register_child_session("unknown-parent", "child-one")

    def test_child_session_cannot_be_rebound_to_another_run(self) -> None:
        self.store.register_child_session(self.root_session, "child-one")
        second_task = "project#8"
        second_run = "b" * 12
        second_root = "other-hermes-root"
        second_nonce = "second-run-nonce"
        now = sandbox_broker._now()
        with self.store.session() as db:
            db.execute("""INSERT INTO runs(task_id,run_id,archive_sha256,source_git_sha,read_only,
                lease_hash,state,archive_name,source_token_hash,lease_nonce,hermes_session_id,profile,
                created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (second_task, second_run, "e" * 64, "f" * 40, 1,
                        sandbox_broker._digest(self.store._derive_lease(second_task, second_run, second_nonce)),
                        "bound", "archive-two.tar.gz", "g" * 64, second_nonce, second_root,
                        "reviewer", now, now, now + 3600))
        with self.assertRaises(sandbox_broker.BrokerError):
            self.store.register_child_session(second_root, "child-one")

    def test_lifecycle_logs_mapping_counts_without_raw_session_ids(self) -> None:
        with self.assertLogs("kanzo.sandbox_broker", level="INFO") as captured:
            self.store.register_child_session(self.root_session, "child-count-test")
            self.store.close_child_session(self.root_session, "child-count-test")

        joined = "\n".join(captured.output)
        self.assertIn("parent_mapping_count=1 child_fp=", joined)
        self.assertIn("child_mapping_count=1", joined)
        self.assertIn("child_mapping_count=0", joined)
        self.assertNotIn(self.root_session, joined)
        self.assertNotIn("child-count-test", joined)

    def test_missing_session_mapping_has_a_specific_error(self) -> None:
        with self.assertRaises(sandbox_broker.BrokerError) as caught:
            self.store.authorize_session("unmapped-session")
        self.assertEqual(caught.exception.code, "session_mapping_not_found")

    def test_ambiguous_session_mapping_has_a_specific_error(self) -> None:
        now = sandbox_broker._now()
        with self.store.session() as db:
            db.execute("INSERT INTO child_sessions(child_session_id,parent_session_id,task_id,run_id,active,created_at) "
                       "VALUES(?,?,?,?,1,?)",
                       (self.root_session, "other-parent", self.task_id, self.run_id, now))
        with self.assertRaises(sandbox_broker.BrokerError) as caught:
            self.store.authorize_session(self.root_session)
        self.assertEqual(caught.exception.code, "session_mapping_ambiguous")


class RunnerHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        with patch("sandbox_broker.KubernetesAPI"):
            self.store = sandbox_broker.BrokerStore(root, root / "broker.sqlite3", b"k" * 32)

    def test_runner_metrics_transition_from_online_to_offline(self) -> None:
        self.store.register_runner("wsl-home", "wsl", now=1000)
        live = self.store.runner_metrics(now=1089)
        stale = self.store.runner_metrics(now=1091)
        self.assertIn('kanzo_runner_up{runner_id="wsl-home",runner_os="wsl"} 1', live)
        self.assertIn('kanzo_runner_up{runner_id="wsl-home",runner_os="wsl"} 0', stale)
        self.assertIn('kanzo_runner_last_seen_timestamp_seconds{runner_id="wsl-home",runner_os="wsl"} 1000', stale)

    def test_runner_identity_is_bounded_and_os_is_validated(self) -> None:
        with self.assertRaises(sandbox_broker.BrokerError):
            self.store.register_runner("../unsafe", "wsl", now=1000)
        with self.assertRaises(sandbox_broker.BrokerError):
            self.store.register_runner("wsl-home", "windows", now=1000)

    def test_http_heartbeat_requires_worker_token_and_metrics_are_readable(self) -> None:
        worker_token = "worker-token-" + "w" * 32
        server = sandbox_broker.BrokerServer(("127.0.0.1", 0), self.store, worker_token, "g" * 48)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        body = json.dumps({"runner_id": "wsl-home", "runner_os": "wsl"}).encode()

        unauthorized = urllib.request.Request(
            base + "/v1/runners/heartbeat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(unauthorized, timeout=2)
        self.assertEqual(caught.exception.code, 401)

        authorized = urllib.request.Request(
            base + "/v1/runners/heartbeat", data=body,
            headers={"Authorization": f"Bearer {worker_token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(authorized, timeout=2) as response:
            self.assertEqual(response.status, 200)
        with urllib.request.urlopen(base + "/metrics", timeout=2) as response:
            metrics = response.read().decode()
        self.assertIn('kanzo_runner_up{runner_id="wsl-home",runner_os="wsl"} 1', metrics)


if __name__ == "__main__":
    unittest.main()
