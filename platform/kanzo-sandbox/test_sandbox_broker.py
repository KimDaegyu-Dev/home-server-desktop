"""Tests for the broker's per-run filesystem boundary."""

from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
