# Video scenes (screen-record these; press Enter between steps)

| Scene | Command (on the VM, in ~/fedsepsis-deploy) | Needs |
|---|---|---|
| a. It's LinuxONE | `video/scene_a_linuxone.sh` | demo stacks running |
| b. Audit before/after re-tune (reverse direction) | `video/scene_b_retune.sh` | reverse stack (`DIRECTION=reverse MODE=real ./run_demo.sh`) |
| c. Secure 3-site run | `video/scene_c_secure.sh` | ~1 min of training (plain + secure), ICU-map split |
| d. Allowed-fields check | `video/scene_d_fields.sh` | forward stack (default `AGG=fedsepsis-aggregator`) |
| e. Hardware features, measured (CPACF + NNPA) | `video/scene_e_hardware.sh` (`LIVE=0` shows saved results) | image `fedsepsis-hospital:nnpa`; ~1.5 min live, 3 GB free RAM |

Use a large terminal font. `NO_PAUSE=1` runs a scene straight through. Scene b leaves the stored re-tuned threshold as
"borrowed re-tuned at 25", the same state as the demo. No asciinema on the VM (no sudo/pip), so no terminal recordings.
