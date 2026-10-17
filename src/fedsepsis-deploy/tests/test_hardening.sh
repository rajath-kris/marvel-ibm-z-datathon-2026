#!/usr/bin/env bash
# B1 / B2 driver: start the hardened test stack (plain or mutual TLS), verify the hardening from `docker inspect` and from
# inside the containers, run the end-to-end checks. Evidence goes to evidence-hardening/ (no environment variables are dumped).
#   ./tests/test_hardening.sh plain      hardening only
#   ./tests/test_hardening.sh mtls       hardening + mutual TLS (./setup_mtls.sh first)
set -uo pipefail
cd "$(dirname "$0")/.."
TAG=${1:-plain}
EV=$PWD/evidence-hardening; mkdir -p "$EV"
export MTLS=$([ "$TAG" = mtls ] && echo 1 || echo 0)
export RUN_DIR=$PWD/run/hardened
RES=()
check() { if eval "$2"; then RES+=("PASS  $1"); else RES+=("FAIL  $1"); fi; echo ">>> ${RES[-1]}"; }

echo "################ start: ./run_hardened.sh (MTLS=$MTLS)"
./run_hardened.sh 2>&1 | tee "$EV/run_$TAG.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "STOP: the stack did not start (see $EV/run_$TAG.txt)"; exit 4; }

echo "################ docker inspect (selected fields only; no environment)"
for c in hard-aggregator hard-hospA hard-hospB; do
  docker inspect "$c" --format '{"name":{{json .Name}},"user":{{json .Config.User}},"readonly_rootfs":{{json .HostConfig.ReadonlyRootfs}},"cap_drop":{{json .HostConfig.CapDrop}},"cap_add":{{json .HostConfig.CapAdd}},"security_opt":{{json .HostConfig.SecurityOpt}},"privileged":{{json .HostConfig.Privileged}},"memory_bytes":{{json .HostConfig.Memory}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids_limit":{{json .HostConfig.PidsLimit}},"tmpfs":{{json .HostConfig.Tmpfs}},"published_ports":{{json .HostConfig.PortBindings}},"networks":[{{range $k,$v := .NetworkSettings.Networks}}{{json $k}},{{end}}"-"],"mounts":{{json .Mounts}},"image":{{json .Config.Image}}}' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); d["networks"]=[n for n in d["networks"] if n!="-"]; d["mounts"]=[{"source":m["Source"],"destination":m["Destination"],"rw":m["RW"]} for m in d["mounts"]]; print(json.dumps(d, indent=1))' \
    > "$EV/inspect_${c#hard-}_$TAG.json"
done
python3 - "$EV" "$TAG" <<'EOF' | tee "$EV/hardening_checks_$TAG.txt"
import json, sys
ev, tag = sys.argv[1:]
ok_all = True
def chk(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
for c in ("aggregator", "hospA", "hospB"):
    d = json.load(open(f"{ev}/inspect_{c}_{tag}.json"))
    chk(f"{c}: runs as non-root", d["user"] == "1000:1000", d["user"])
    chk(f"{c}: read-only root filesystem", d["readonly_rootfs"] is True)
    chk(f"{c}: --cap-drop ALL and no capability added", d["cap_drop"] == ["ALL"] and not d["cap_add"], str(d["cap_drop"]))
    chk(f"{c}: no-new-privileges, not privileged", any("no-new-privileges" in s for s in d["security_opt"]) and d["privileged"] is False, str(d["security_opt"]))
    chk(f"{c}: memory, CPU and pids limits set", d["memory_bytes"] > 0 and d["nano_cpus"] > 0 and (d["pids_limit"] or 0) > 0,
        f"memory {d['memory_bytes'] >> 20} MiB, cpus {d['nano_cpus'] / 1e9:g}, pids {d['pids_limit']}")
    chk(f"{c}: scratch space is a tmpfs", "/tmp" in (d["tmpfs"] or {}), str(d["tmpfs"]))
    chk(f"{c}: only its private network hard-net", d["networks"] == ["hard-net"], str(d["networks"]))
    rw = [m["destination"] for m in d["mounts"] if m["rw"]]
    chk(f"{c}: writable mounts are only its output/state volume", set(rw) <= {"/out", "/state"}, f"rw mounts {rw}")
    ports = {k: v for k, v in (d["published_ports"] or {}).items() if v}
    want = {"8100/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18102"}]} if c == "hospB" else {}
    chk(f"{c}: published ports {'only 127.0.0.1:18102' if c == 'hospB' else 'none'}", ports == want, str(ports))
sys.exit(0 if ok_all else 1)
EOF
HC=$?
RES+=("$([ $HC -eq 0 ] && echo PASS || echo FAIL)  docker inspect hardening checks (see hardening_checks_$TAG.txt)")

echo "################ inside the containers"
{
  for c in hard-aggregator hard-hospA hard-hospB; do
    echo "--- $c"
    docker exec "$c" sh -c 'echo "uid=$(id -u) gid=$(id -g)"; grep -E "^(CapEff|CapPrm|CapBnd|NoNewPrivs)" /proc/self/status; (touch /app/should-fail 2>&1 || true); (touch /tmp/ok && echo "/tmp writable (tmpfs)"); (touch /data/should-fail 2>&1 || true)'
  done
} 2>&1 | tee "$EV/inside_containers_$TAG.txt"
check "uid 1000 in every container" '[ "$(grep -c "^uid=1000 gid=1000" "$EV/inside_containers_$TAG.txt")" -eq 3 ]'
check "CapEff is empty in every container" '[ "$(grep -c "^CapEff:.0000000000000000" "$EV/inside_containers_$TAG.txt")" -eq 3 ]'
check "NoNewPrivs is 1 in every container" '[ "$(grep -c "^NoNewPrivs:.1" "$EV/inside_containers_$TAG.txt")" -eq 3 ]'
check "writing to the root filesystem fails (/app)" '[ "$(grep -c "Read-only file system" "$EV/inside_containers_$TAG.txt")" -ge 3 ]'
check "hospital data mount is read-only (/data)" 'grep -q "touch: cannot touch .\/data\/should-fail.: Read-only file system" "$EV/inside_containers_$TAG.txt"'

echo "################ end-to-end checks"
python3 tests/hardened_e2e.py "$TAG" 2>&1 | tee "$EV/e2e_$TAG.txt"
RES+=("$([ "${PIPESTATUS[0]}" -eq 0 ] && echo PASS || echo FAIL)  end-to-end checks (see e2e_$TAG.txt)")

echo "================ RESULTS ($TAG) ================" | tee "$EV/summary_$TAG.txt"
printf '%s\n' "${RES[@]}" | tee -a "$EV/summary_$TAG.txt"
! printf '%s\n' "${RES[@]}" | grep -q '^FAIL'
