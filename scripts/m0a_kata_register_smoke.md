# M0(a) — Kata runtime registration + smoke test (SERVER, run in PuTTY)

Goal of this step: register Kata as a **named, non-default** Docker runtime and prove a container
actually boots in a VM (guest kernel ≠ host kernel). This is the longest-lead M0 item (it needs a
maintenance window for the daemon reload), so start it first. The fuller probe checklist
(virtio-fs mounts, caps, VM→proxy→vLLM reachability) lands in Slice 3.

**Pre-flight facts (from the design spec — re-confirm, don't trust blindly):**
- Kata 3.31.0 at `/opt/kata`; `/dev/kvm` present; kernel 6.8 ⇒ cgroup v2.
- Use **QEMU** (the only Docker-tested VMM). Never change the **default** runtime. No GPU passthrough.
- `systemctl reload docker` re-reads `daemon.json` via SIGHUP **without restarting** running
  containers — gentle, but still do it in a window where depot can blink.

---

## 0. Verify Kata is healthy and find the shim path

```bash
/opt/kata/bin/kata-runtime check                 # should pass (KVM available, etc.)
ls -l /opt/kata/bin/containerd-shim-kata-v2       # the runtimeType target must exist
docker info --format '{{json .Runtimes}}' | tr ',' '\n'   # current runtimes (expect runc + nvidia)
docker compose version                            # confirm compose v2 (depot needs it)
```

## 1. Back up and inspect the current daemon.json

```bash
sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null \
  || echo "no existing daemon.json (will create one)"
sudo cat /etc/docker/daemon.json 2>/dev/null || echo '{}'
```

> ⚠️ **Merge, do not clobber.** If `daemon.json` already defines `runtimes` (e.g. `nvidia`), keep
> them. The result must ADD a `kata` runtime and leave the default runtime untouched.

## 2. Add the kata runtime (named, non-default)

**Option A — jq merge (safe if `jq` is installed):**
```bash
sudo sh -c 'jq ".runtimes.kata = {\"runtimeType\": \"/opt/kata/bin/containerd-shim-kata-v2\"}" \
  /etc/docker/daemon.json 2>/dev/null || \
  echo "{\"runtimes\":{\"kata\":{\"runtimeType\":\"/opt/kata/bin/containerd-shim-kata-v2\"}}}"' \
  | sudo tee /etc/docker/daemon.json.new
sudo cat /etc/docker/daemon.json.new          # eyeball it: nvidia (if any) still present, kata added
sudo mv /etc/docker/daemon.json.new /etc/docker/daemon.json
```

**Option B — manual edit** (if no jq, or you prefer to see it): `sudo nano /etc/docker/daemon.json`
and ensure it contains (merged with whatever was already there):
```json
{
  "runtimes": {
    "kata": { "runtimeType": "/opt/kata/bin/containerd-shim-kata-v2" }
  }
}
```
Validate JSON before reloading:
```bash
python3 -c 'import json,sys; json.load(open("/etc/docker/daemon.json")); print("daemon.json OK")'
```

## 3. Reload Docker (the scheduled window)

```bash
sudo systemctl reload docker
docker info --format '{{json .Runtimes}}' | tr ',' '\n'   # expect runc, nvidia, AND kata now
docker info --format 'default-runtime={{.DefaultRuntime}}' # MUST still be runc (unchanged)
```
If reload doesn't pick it up on this Docker build, fall back to a full restart **in the window**:
`sudo systemctl restart docker` (this DOES bounce containers — bring depot back after).

## 4. Smoke test — prove it's a VM

```bash
echo "HOST kernel:  $(uname -r)"
echo "KATA guest:   $(docker run --runtime kata --rm ubuntu:24.04 uname -r)"
```
**PASS criterion:** the two kernel strings **differ** (the kata line shows the Kata guest kernel, not
the host's). Same string ⇒ it silently ran under runc — investigate before proceeding.

Quick extra sanity (optional):
```bash
docker run --runtime kata --rm ubuntu:24.04 sh -c 'echo in-vm; cat /proc/1/cgroup | head'
```

---

## Record the result

Paste back into this chat: the two kernel strings, the `Runtimes` list, and the `default-runtime`
line. I'll log the outcome in `DECISIONS.md` and tick the M0(a) smoke box in `ROADMAP.md`, then hand
you the Slice-3 probe checklist (virtio-fs uid/gid, nested RO `tests/` mount, caps +
`sandbox_cgroup_only` on cgroup v2, named-volume uv cache, VM→proxy→vLLM on an internal network).

**Fallbacks (if needed — document, don't block):** if `sandbox_cgroup_only` misbehaves on cgroup v2,
host-side cgroups are the authoritative cap. If virtio-fs mounts misbehave later, the fallback is a
tar-based exec bridge.
