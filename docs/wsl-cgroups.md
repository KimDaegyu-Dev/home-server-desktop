# WSL cgroup v2

K3s 1.37 requires cgroup v2. Keep systemd enabled in Ubuntu's `/etc/wsl.conf` and merge [`wsl/.wslconfig`](../wsl/.wslconfig) into `%UserProfile%\.wslconfig` on Windows. The file is global to all WSL2 distributions, so preserve any existing settings and merge the `[wsl2]` keys rather than overwriting them.

After changing the file, run `wsl --shutdown` from PowerShell, then start Ubuntu again. Confirm cgroup v2 with:

```bash
stat -fc %T /sys/fs/cgroup
```

Expected output: `cgroup2fs`.


On WSL 2.4.13, vmIdleTimeout=-1 in wsl/.wslconfig disables the VM idle timeout used by this K3s host. Merge the example into the existing Windows configuration, then run wsl --shutdown to apply it.
