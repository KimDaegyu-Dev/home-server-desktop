# WSL cgroup v2

K3s 1.37 requires cgroup v2. Keep systemd enabled in Ubuntu's `/etc/wsl.conf` and merge [`wsl/.wslconfig`](../wsl/.wslconfig) into `%UserProfile%\.wslconfig` on Windows. The file is global to all WSL2 distributions, so preserve any existing settings and merge the `[wsl2]` keys rather than overwriting them.

The WSL virtual NIC defaults to MTU 1280, which can truncate nested Tailscale traffic and make TLS handshakes stall. The WSL host preparation playbook installs `home-server-wsl-mtu.service` to set `eth0` to MTU 1500 before K3s starts. This matches the Windows `vEthernet (WSL)` MTU and lets Flannel allocate a 1450 MTU to Pod interfaces.

After changing the file, run `wsl --shutdown` from PowerShell, then start Ubuntu again. Confirm cgroup v2 with:

```bash
stat -fc %T /sys/fs/cgroup
```

Expected output: `cgroup2fs`.

## 메모리 회수

`wsl/.wslconfig`는 WSL VM의 최대 메모리를 32GB로 제한하고 `[experimental] autoMemoryReclaim=gradual`을 설정합니다. 이는 현재 K3s 노드의 약 14GiB 사용량에 여유를 두면서 WSL이 메모리를 계속 키우는 것을 막고, 사용하지 않는 파일 캐시는 Windows로 천천히 반환합니다. 이 제한은 Windows 호스트의 모든 WSL2 배포판에 적용됩니다. Kubernetes에서 실제로 사용하는 메모리와 Strata 모델 메모리 자체를 제한하지는 않습니다.

설정을 바꾼 뒤 WSL VM이 완전히 재시작되어야 적용됩니다. `wsl --shutdown`은 모든 WSL 배포판을 종료하므로 K3s 점검 시간에 실행해야 합니다. 재기동 후 Vault는 Shamir 방식으로 수동 unseal합니다.


On WSL 2.4.13, vmIdleTimeout=-1 in wsl/.wslconfig disables the VM idle timeout used by this K3s host. Merge the example into the existing Windows configuration, then run wsl --shutdown to apply it.
