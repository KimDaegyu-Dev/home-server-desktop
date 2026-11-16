"""Tests for the broker's per-run filesystem boundary."""

from __future__ import annotations

import tempfile
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


if __name__ == "__main__":
    unittest.main()
