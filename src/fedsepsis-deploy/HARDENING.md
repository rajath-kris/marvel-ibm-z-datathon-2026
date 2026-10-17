# Hardening and mutual TLS (branch `hardening-tls`)

What this adds on top of the federated deployment, how to run it, and what was verified on the LinuxONE VM
(datathon-test, s390x). The work was started by Reju and finished (B4) by Adhil after a handover. All evidence is in
`evidence-hardening/`.

**Claims.** "Hardened containers" and "mutual TLS between hospitals and the aggregator". **Not** claimed: on-chip crypto
(CPACF). It was not confirmed (see B3). Also not claimed: Secure Execution, which is not active on this VM.

## What changed

| File | Change |
|---|---|
| `demo_lib.sh` | `HARDEN=1`: every container runs as `--user 1000:1000` with `--read-only` (root filesystem), a `tmpfs` at `/tmp` (`noexec,nosuid,nodev`, 128 MB, `HOME=/tmp`), `--cap-drop ALL`, `--security-opt no-new-privileges:true` and `--pids-limit 256`, on top of the existing memory/CPU limits and `:ro` data mounts. `MTLS=1`: mounts the CA and each container's own certificate read-only. Hospitals with no published port are health-checked through `docker exec`. |
| `aggregator/aggregator.py` | `TLS_CLIENT_CA` makes the TLS 1.3 server **require** a client certificate signed by that CA (`CERT_REQUIRED`). The certificate's CN must equal the `site_id` in every POST (403 otherwise). It logs one line per negotiated session (client CN, version, cipher) and one line per refused connection, never a traceback. The TLS handshake runs in the request thread under a 30 s timeout, not in the accept loop. |
| `hospital/client.py` | Presents its own client certificate (`TLS_CLIENT_CERT`/`TLS_CLIENT_KEY`). Keeps the aggregator leaf-certificate pin. Logs the negotiated version and cipher once. |
| `setup_mtls.sh` | Throwaway CA plus certificates, **outside the project** in `$SECRET_DIR/mtls/`: the CA key (600, never mounted), the aggregator server certificate, client certificates per site plus `monitor` (harness health check only), and a `rogue/` certificate from an untrusted CA for the refusal test. |
| `run_hardened.sh`, `stop_hardened.sh` | A separate test stack, `hard-aggregator`/`hard-hospA`/`hard-hospB` on `hard-net`, with images `:hard` and its own token and certificates in `~/.fedsepsis-hardening`. It never touches the live deployment. |
| `tests/test_hardening.sh`, `tests/hardened_e2e.py`, `tests/test_mtls.sh`, `tests/test_cpacf.sh`, `tests/cpacf_hint.sh` | B1 to B3 checks (below). |
| `.gitignore` | Adds `*.csr`, `*.srl`, `.fedsepsis-hardening/` and `run/hardened/`. Certificates and keys were already excluded by `*.pem`/`*.key`/`*.crt`. |

The demo port is unchanged: hospital `/score`, `/audit`, `/retune` and `/patient` stay **plain HTTP on 127.0.0.1**
(through the ssh tunnel), so `DEMO_API.md` and the demo builder's calls don't change. Mutual TLS covers the
hospital-to-aggregator traffic inside the docker network.

## How to run

```bash
./setup_mtls.sh                          # once: CA + certificates in ~/.fedsepsis-hardening/mtls (prints subjects/fingerprints only)
./run_hardened.sh                        # hardening only (plain HTTP inside hard-net, like the live demo)
MTLS=1 ./run_hardened.sh                 # hardening + mutual TLS
./tests/test_hardening.sh plain          # B1: inspect + in-container checks + 28 end-to-end checks
./tests/test_hardening.sh mtls           # B1 + B2 with mutual TLS
./tests/test_mtls.sh                     # B2: refusal tests against the running mTLS stack
./tests/test_cpacf.sh                    # B3: cpacfstats before/after one round (see verdict)
./stop_hardened.sh                       # removes only hard-* containers and hard-net
```

Memory gate (the VM is shared): `run_hardened.sh` refuses to start below 2500 MB available. Below 3000 MB it drops to a
low-memory profile (1g / 1g / 512m, 5 rounds).

## B1: container hardening, PASS

`evidence-hardening/hardening_checks_{plain,mtls}.txt`: **27/27** `docker inspect` checks pass in both modes, for each
of the aggregator, hospA and hospB:
- non-root `1000:1000`
- read-only root filesystem
- `CapDrop [ALL]` and nothing added
- `no-new-privileges:true` and not privileged
- memory 512 MiB / 2048 MiB / 1024 MiB, CPU 1 and pids 256 limits
- a `/tmp` tmpfs
- only the private network `hard-net`
- the only writable mount is its own `/state` or `/out`
- no published ports, except hospB on `127.0.0.1:18102`

`evidence-hardening/inside_containers_{plain,mtls}.txt`, checked from inside every container:
- `uid=1000`
- `CapEff 0000000000000000`
- `NoNewPrivs 1`
- writing to `/app` fails ("Read-only file system")
- `/tmp` is writable
- writing to the hospital's `/data` fails ("Read-only file system")

End to end, `evidence-hardening/e2e_{plain,mtls}.txt`: **28/28 pass in both modes**. That's 40 of 40 rounds, an identical
final model at both sites, input validation (400s), and these numbers:
- audit borrowed **11.666600**
- re-tuned **23.995858** (threshold 0.42531526551518406)
- federated **22.453905**
- replays of patients 71 and 13561 match `demo_patient.json`
- CORS
- the allowed-fields check

The rates are identical to the live (unhardened) deployment's saved evidence. Runtime: 50.9 s plain, 43.9 s mTLS (40 rounds).

## B2: mutual TLS, PASS

`evidence-hardening/test_mtls.txt` and `summary_mtls_refusal.txt`: **10/10**.

| Check | Result |
|---|---|
| No client certificate | refused at the TLS layer (`tlsv13 alert certificate required`) |
| Client certificate from an untrusted CA | refused (`tlsv1 alert unknown ca`) |
| Valid certificate, no token | TLS OK, then HTTP 401 (the token is still required) |
| Valid certificate + token | 200 |
| `site_id` not equal to the certificate CN | 403 |
| TLS 1.2 | refused |
| Plain HTTP | no HTTP response |
| Aggregator log | one `tls_session` line per client: `client_cn=hospA` / `hospB` / `monitor`, `TLSv1.3`, `TLS_AES_256_GCM_SHA384`; refused connections logged |
| Hospitals | present their certificates (`client_cert=True`) |

## B3: CPACF (on-chip crypto assist): NOT CONFIRMED

The rule was fixed before running (`tests/test_cpacf.sh`): **CPACF USED** only if `cpacfstats` can be read before and
after one full mTLS round **and** at least one counter increases. Otherwise the verdict is **NOT CONFIRMED**.

What happened (`evidence-hardening/cpacf_verdict.txt`, `cpacfstats_before.txt`, `cpacfstats_after.txt`,
`cpacf_environment.txt`):
- `cpacfstats` could not read any counter before or after the round: "Can't access domain socket file
  '/run/cpacfstatsd_socket' ... Maybe cpacfstatsd daemon is not running". `linux1` is in the `cpacfstats` group, but the
  daemon is **inactive**, and starting it needs root (no sudo here).
- Unprivileged `perf` is off (`kernel.perf_event_paranoid=4`), so the PAI crypto counters can't be read that way either.
- Verdict, as recorded: **`VERDICT: CPACF NOT CONFIRMED`**.

A supplementary observation, which is **not** the verdict (`cpacf_indirect_hint.txt`): with OpenSSL's s390x CPU-capability
masks zeroed (`OPENSSL_s390xcap`), AES-256-GCM drops from about 15.1 GB/s to 0.14 GB/s, and SHA-256 inside the hardened
hospital image drops from 3965 to 258 MB/s. That suggests OpenSSL **can** use CPACF on this machine. It does **not** show
that a federated round did. The bare value `off` had no effect on this OpenSSL (`cpacf_indirect_off_switch_no_effect.txt`),
so it was discarded. **We do not claim on-chip crypto.**

## What this does not protect

- **The token, CA key and TLS keys are files on the VM.** Anyone with docker or root access on the VM can read them.
  Keys are mode 600 and outside the project.
- **The demo HTTP port on 127.0.0.1 is unauthenticated.** It is reachable only from the VM or through the ssh tunnel.
- **No Secure Execution:** the host and hypervisor can see container memory.
- **The CA is a throwaway** (short-lived, self-made). There's no revocation and no hardware key storage.
- **Hardening is not tamper-proof.** It limits what a compromised process inside a container can do; it doesn't stop a
  privileged user on the host.

## B4: hygiene (this commit)

- **Scan before committing:** no PEM blocks, and no `.pem`/`.key`/`.crt`/`.csr`/`.srl`/`.seed` files in the worktree.
  None of the 2 tokens, 3 pair seeds or 8 private keys on the VM appears in any file or in the git history (matched by
  content and listed by name, never printed).
- **Certificates and the CA** stay in `~/.fedsepsis-hardening/mtls/` (and `~/.fedsepsis/mtls/` for the live stack after
  the merge), outside the repository and git-ignored.
- **Test stack removed:** the `hard-*` containers and `hard-net` are gone (`./stop_hardened.sh`).
