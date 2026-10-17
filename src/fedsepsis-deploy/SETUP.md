# Set up Dr Sepsis from scratch (for a new person or a new machine)

This walks through everything needed to run the whole thing yourself: both directions, NEWS2, the secure 3-site demo,
hardening with mutual TLS, the tests and the video scenes.

**No keys are shared.** Every secret (API token, CA and certificates, pair seeds) is generated on your machine by the
setup scripts in step 4 and never leaves it. Don't ask anyone for theirs, and never commit or paste yours.

## 1. What you need (none of it is a key)

| You need | Where it comes from |
|---|---|
| **A machine to run on** | **The IBM LinuxONE VM is the tested path** (section A). A laptop is possible but not yet tested (section B). |
| **VM access** (path A only) | Your **own** SSH access from the datathon organisers. The SSH key is personal and is not part of this project. |
| **The code** | `git clone -b feat/fedsepsis-deploy https://github.com/rajath-kris/marvel-ibm-z-datathon-2026.git`. The deployment is in `src/fedsepsis-deploy/`. |
| **The data bundle** | From Reju (the "handoff"), or rebuilt from public PhysioNet data (README, "How to get the data"). It is **not** in the repository. |
| **Docker** | Already on the VM (Docker 29.8; your user must be in the `docker` group). On a laptop: Docker Desktop or Docker Engine. |

Expected layout of the data bundle (paths are the defaults in `config.sh`):
```
~/handoff/fedsepsis/                 the library (fedsepsis 0.2.0: __init__.py, VERSION, test_fedsepsis.py)
~/handoff/outputs/demo/              forward direction (hospital B small): *.npz, model_*.json, demo_patient.json,
                                     news2_scores.npz, news2_thresholds.json, repro_config.json, HANDOFF.md, SHA256SUMS.txt
~/handoff/outputs/demo_reverse/      reverse direction (hospital A small): same files
~/handoff/icu_map.json               patient -> hospA1 (medical ICU) / hospA2 (surgical ICU), for the secure demo
```
The bundle holds rows derived from the PhysioNet/CinC 2019 challenge data. Treat it like the source data: keep it out of
git and off public places, and follow PhysioNet's terms.

## A. On the LinuxONE VM (tested, 2026-10-18)

**A1. SSH alias on your laptop.** Use your own key and the address the organisers give you:
```
Host l1cc
    HostName <vm-address>
    User <your-user>
    IdentityFile <path-to-your-own-key>
    IdentitiesOnly yes
```
Run `chmod 600 <path-to-your-own-key>`. Test it with `ssh l1cc "uname -m"`, which should print `s390x`.

**A2. Code and data onto the VM.**
```bash
ssh l1cc "git clone -b feat/fedsepsis-deploy https://github.com/rajath-kris/marvel-ibm-z-datathon-2026.git ~/dr-sepsis \
          && ln -sfn ~/dr-sepsis/src/fedsepsis-deploy ~/fedsepsis-deploy"
scp -r handoff l1cc:~/                                  # the bundle from Reju, if it isn't on the VM already
```
The link assumes `~/fedsepsis-deploy` doesn't exist yet. If it does (like on the team VM), use that folder, or clone
somewhere else and run the scripts from `~/dr-sepsis/src/fedsepsis-deploy`. The scripts work from any folder.

**A3. Check the data.** Every file must say OK:
```bash
ssh l1cc "cd ~/handoff/outputs/demo && sha256sum -c SHA256SUMS.txt && cd ../demo_reverse && sha256sum -c SHA256SUMS.txt"
```

**A4. Create your own secrets and install the library.** These are generated on the VM, mode 600, in `~/.fedsepsis/`:
```bash
ssh l1cc "cd ~/fedsepsis-deploy && ./setup_token.sh && ./setup_real.sh && DIRECTION=reverse ./setup_real.sh"
```
`run_live.sh` creates the mTLS CA and certificates on first use (`setup_mtls.sh`). The secure demo creates fresh pair seeds
on every run (`setup_pairs.sh`).

**A5. Start both live directions** (hardened, mutual TLS):
```bash
ssh l1cc "cd ~/fedsepsis-deploy && free -m && ./run_live.sh"
```
- This takes about 1.5 minutes.
- It refuses to start below 3 GB available memory, or next to unexpected containers. The VM is shared, so don't
  override that lightly.
- The Jupyter container on port 38888 is shared. **Never stop or touch it.**

**A6. Check the numbers against the experiment.** Every line should say PASS:
```bash
ssh l1cc "cd ~/fedsepsis-deploy && python3 tests/check_direction.py http://127.0.0.1:8102 ~/handoff/outputs/demo \
          && python3 tests/check_direction.py http://127.0.0.1:8202 ~/handoff/outputs/demo_reverse"
```
Expected:
- forward: borrowed 11.666600, re-tuned 23.995858, federated 22.453905, NEWS2 24.531082
- reverse: borrowed 93.547483, re-tuned 27.157763, federated 25.188213, NEWS2 30.729865

**A7. Use the demo from your laptop.**
```bash
ssh -N -L 8102:127.0.0.1:8102 -L 8202:127.0.0.1:8202 l1cc
```
Leave that running, then open <http://localhost:8102/health>. The API is in `DEMO_API.md`.

**A8. Secure 3-site demo, tests, video, evidence.**
```bash
ssh l1cc "cd ~/fedsepsis-deploy && SPLIT_MAP=~/handoff/icu_map.json ./tests/test_secure_e2e.sh"   # secure = plain, about 1e-16
ssh l1cc "cd ~/fedsepsis-deploy && ./tests/verify_merge.sh"        # full check on a separate test stack (mtest-*)
ssh -t l1cc "cd ~/fedsepsis-deploy && video/scene_a_linuxone.sh"  # scenes a-d, press Enter between steps (video/README.md)
ssh l1cc "cd ~/fedsepsis-deploy && ./collect_evidence.sh && python3 tools/evidence_board.py evidence_board.html"
```

**A9. Stop.**
```bash
ssh l1cc "cd ~/fedsepsis-deploy && ./stop_demo.sh && DIRECTION=reverse ./stop_demo.sh"
```

## B. On a laptop: NOT TESTED yet

The code was built for and tested only on the LinuxONE VM. Running it on a laptop needs these changes first. They are
known, but nobody has verified them:

1. **A base image for your processor.**
   - Problem: both Dockerfiles and several tests use `registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x`,
     which is built for IBM Z only.
   - Fix: build an amd64/arm64 base image that has Python 3.12 with **numpy** in a venv at `/opt/venv/bin/python`, plus
     `curl`, a user with uid 1000, and the same paths. Make the Dockerfiles' `FROM` use it (for example through
     `ARG BASE_IMAGE`), and set `BASE_IMAGE` in `config.sh`.
2. **Linux shell tools.**
   - Problem: the scripts use bash 4+ features (associative arrays), GNU `realpath -m`, `date +%s.%N` and
     `/proc/meminfo` (the memory pre-flight).
   - Fix: Linux or WSL2 should work as is. macOS needs `brew install bash coreutils` plus a change to the pre-flight,
     because macOS has no `/proc/meminfo`.
3. **Resources:** about 4 to 6 GB of memory for Docker, for one direction plus tests.
4. **What changes in the results.**
   - The model numbers should match, but this is not yet tested for the deployment: the library itself agreed between a
     laptop and the VM to 1.3e-15 (`HANDOFF.md`).
   - The LinuxONE-specific evidence does **not** apply on a laptop: s390x, machine type 9175, nnpa, the torch NNPA check
     and the CPACF notes.

If you need the laptop path, ask for it to be implemented and tested. Until then, use path A.

## Where things are documented

| File | Contents |
|---|---|
| `README.md` | Architecture, the current live deployment, every option, the tests, what is not protected, where secrets live |
| `HARDENING.md` | Container hardening, mutual TLS, the CPACF verdict (and how an admin can turn it into a real claim) |
| `DEMO_API.md` | The hospital endpoints the demo page calls |
| `video/README.md` | The four video scenes |
| `~/handoff/outputs/*/HANDOFF.md` | Reju's description of the data, models and expected numbers (ships with the bundle) |
