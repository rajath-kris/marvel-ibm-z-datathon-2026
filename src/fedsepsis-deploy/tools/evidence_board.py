"""Build evidence_board.html (one page, large text, for screenshots) from evidence/*.txt and demo/secure_summary.json.
Standard library only. Usage: python3 tools/evidence_board.py OUT.html"""
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

E = "evidence/"
read = lambda f: open(E + f).read()
esc = html.escape


def body(text):  # drop the "# title / # captured" header lines that collect_evidence.sh adds
    return "\n".join(l for l in text.splitlines() if not l.startswith("# ")).strip("\n")


def captured(text):
    m = re.search(r"# captured (\S+) on (\S+)", text)
    return f"captured {m.group(1)} on {m.group(2)}" if m else ""


cpu = read("cpu_features.txt")
features = next(l for l in cpu.splitlines() if l.startswith("features"))
feat_html = esc(features).replace("nnpa", '<mark>nnpa</mark>')
lscpu = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in cpu.splitlines() if ":" in l and not l.startswith(("#", "features")))}
uname = body(read("uname_m.txt"))
torch = read("torch_nnpa.txt")
torch_out = body(torch).splitlines()[-1].strip()
ps = body(read("docker_ps.txt"))
agg = body(read("aggregator_log.txt")).splitlines()
agg_excerpt = agg[:8] + ["  ...  (" + str(sum("event=round_complete" in l for l in agg)) + " rounds completed)  ..."] + agg[-3:]
ends = read("test_real_endpoints.txt")
fields = ends[ends.index("=== (f) allowed fields on the real run"):].split("\n", 1)[1].strip()
sec = json.load(open("demo/secure_summary.json"))
hard = read("hardening_flags.txt") if os.path.exists(E + "hardening_flags.txt") else ""
pass_fields = "ASSERT subset of allowed : True" in fields


def card(title, inner, note=""):
    return f'<section><h2>{esc(title)}</h2>{inner}<p class="note">{note}</p></section>'


rows = "".join(
    f"<tr><td>{esc(s)}</td><td>{esc(sec['site_description'].get(s, ''))}</td><td class=num>{sec['site_patients'].get(s, ''):,}</td>"
    f"<td class=mono>{'  '.join(f'{x:.4f}' for x in v['masked_first3_fraction'])}</td></tr>"
    for s, v in sorted(sec["what_the_aggregator_saw"]["per_site"].items()))
fm = sec["final_model_first3"]
cards = [
    card("Platform: IBM LinuxONE",
         f'<div class=big><span>{esc(uname)}</span><span>Machine type {esc(lscpu.get("Machine type", "?"))}</span>'
         f'<span>{esc(lscpu.get("Byte Order", ""))}</span><span>{esc(lscpu.get("CPU(s)", "?"))} vCPUs</span></div>'
         f'<pre class=wrap>{feat_html}</pre>',
         f"uname -m, lscpu and the features line of /proc/cpuinfo. {esc(captured(cpu))}"),
    card("AI accelerator visible to PyTorch",
         f'<div class=big><span class=ok>{esc(torch_out)}</span></div>'
         '<pre>torch.nnpa.is_available(), torch.nnpa.is_telum2_available()</pre>',
         f"Run in the Jupyter image on the VM, reported as is. By default our logistic-regression model does not use it; "
         f"the optional NNPA scoring path is in the panel further down. {esc(captured(torch))}"),
    card("Container hardening + mutual TLS", f"<pre>{esc(body(hard))}</pre>",
         "From docker inspect on the live containers: non-root 1000:1000, read-only root filesystem, all capabilities dropped, "
         "no-new-privileges, pids limit, demo ports only on 127.0.0.1. Hospital-to-aggregator traffic is mutual TLS 1.3 "
         "(client certificate required, CN = site). Whether our own traffic used on-chip crypto (CPACF) is NOT claimed: "
         "the hardware counters could not be read (see the CPACF panel for what was measured). "
         f"{esc(captured(hard))}") if hard else "",
    card("Containers running", f"<pre>{esc(ps)}</pre>",
         f"docker ps: one aggregator + one container per hospital, each direction. Hospital /score ports are bound to "
         f"127.0.0.1 only. {esc(captured(read('docker_ps.txt')))}"),
    card("Aggregator log (excerpt)", f"<pre>{esc(chr(10).join(agg_excerpt))}</pre>",
         "One line per event: time, site, round, and the request-body KEYS only. No values, no patient data."),
    card("Allowed-fields check: " + ("PASS" if pass_fields else "FAIL"),
         f'<pre>{esc(fields)}</pre>',
         "Every key the aggregator received during the real 40-round run is in {site_id, n, sum, sumsq, round, w, b}: "
         "counts, sums and model weights. No patient rows cross the network."),
    card("Secure aggregation: 3 sites",
         f"<table><tr><th>site</th><th></th><th>patients</th><th>what the aggregator saw (first 3, as fraction of 2<sup>128</sup>)</th></tr>{rows}</table>"
         f'<div class=big><span class=ok>secure = plain, max difference {sec["max_abs_difference_secure_vs_plain"]:.1e}</span>'
         f'<span>{sec["rounds"]} rounds</span><span>secure {sec["runtime_seconds"]["secure_run"]} s / plain {sec["runtime_seconds"]["plain_run"]} s</span></div>'
         f'<pre>first 3 final weights  secure {", ".join(f"{x:.10f}" for x in fm["secure_run"])}\n'
         f'                       plain  {", ".join(f"{x:.10f}" for x in fm["plain_run"])}</pre>',
         esc(sec["simulation_note"]) + f" Generated {esc(sec['generated_utc'])}."),
]
# ---- CPACF benchmark on our own mTLS settings, and optional NNPA scoring (files written by tests/cpacf_bench.sh and tests/test_nnpa_parity.py)
H = "evidence-hardening/"


def opt(f):
    return open(H + f).read() if os.path.exists(H + f) else ""


def bench_sections(text):
    out, cur = {}, None
    for l in text.splitlines():
        if l.startswith("## "):
            cur = l[3:].strip(); out[cur] = {}
        elif cur:
            for key, pat in (("bulk", r"median (\d+) MB/s"), ("hs", r"handshakes: (\d+)/s"), ("absorb", r"absorb: (\d+) MB/s"), ("squeeze", r"squeeze.*?: (\d+) MB/s")):
                m = re.search(pat, l)
                if m and (key in ("bulk", "hs", "absorb", "squeeze")) and (key != "bulk" or "bulk" in l) and (key != "hs" or "handshakes" in l):
                    out[cur][key] = int(m.group(1))
    return out


bench = opt("cpacf_tls_bench.txt")
bs = bench_sections(bench)
extra_cards = []
if "default" in bs and "masked" in bs and all(k in bs["default"] and k in bs["masked"] for k in ("bulk", "hs", "absorb", "squeeze")):
    d, m = bs["default"], bs["masked"]
    names = (("bulk", "mutual-TLS 1.3 bulk transfer (MB/s)"), ("hs", "new mutual-TLS connections per second"),
             ("absorb", "SHAKE-256 hashing, as used by the secure-aggregation masks (MB/s)"), ("squeeze", "SHAKE-256 mask stream (MB/s)"))
    trs = "".join(f"<tr><td>{esc(t)}</td><td class=num>{d[k]:,}</td><td class=num>{m[k]:,}</td><td class=num><b>{d[k] / m[k]:.1f}x</b></td></tr>" for k, t in names)
    extra_cards.append(card("On-chip crypto (CPACF): measured on our own mutual-TLS settings",
        f"<table><tr><th></th><th class=num>CPACF on (default)</th><th class=num>CPACF masked</th><th class=num>speed-up</th></tr>{trs}</table>"
        f"<pre>{esc(chr(10).join(l for l in bench.splitlines() if l.startswith(('negotiated', 'openssl'))))}</pre>",
        "Same TLS settings as the aggregator (TLS 1.3 only, client certificate required) inside the hardened hospital image; "
        "'masked' sets every CPACF capability mask to zero (OPENSSL_s390xcap). This shows the TLS stack our containers use runs "
        "faster with CPACF. It does NOT show that a federated round used it: the hardware counters (cpacfstats) need an admin "
        "daemon we cannot start, so that stays unconfirmed. " + esc(bench.splitlines()[0].lstrip("# ") if bench else "")))
keep = ("scores:", "alert rate", "retuned", "time,", "PARITY", "per-patient", "RESULT", "data:")
nn = ""
for f, lab in (("nnpa_parity_forward.txt", "forward (hospital B small, borrows A's model)"), ("nnpa_parity_reverse.txt", "reverse (hospital A small, borrows B's model)")):
    t = opt(f)
    if t:
        nn += f"<h3>{esc(lab)}</h3><pre class=wrap>{esc(chr(10).join(l for l in t.splitlines() if l.startswith(keep)))}</pre>"
if nn:
    extra_cards.append(card("Optional NNPA scoring: CPU vs accelerator on the real demo data", nn,
        "Off by default (NNPA_SCORE=1 switches it on). Only the scoring product runs on the accelerator; the library, training and all "
        "default numbers are unchanged. Result as measured: it runs on the NNPA and stays within the 0.1 alerts per 100 patient-days "
        "limit, but it is NOT faster for this small model and uses reduced precision, so we do not claim a speed-up."))
cards += extra_cards

page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Dr Sepsis Evidence Board</title><style>
:root{{--bg:#0f1419;--card:#182029;--ink:#e8edf2;--dim:#9aa7b4;--ok:#5fd38d;--mark:#ffd166;--line:#2a3542}}
body{{margin:0;background:var(--bg);color:var(--ink);font:20px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 60px}} h1{{font-size:38px;margin:0 0 4px}}
.sub{{color:var(--dim);margin:0 0 24px}} section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:0 0 22px}}
h2{{font-size:26px;margin:0 0 14px}} h3{{font-size:20px;color:var(--dim);margin:14px 0 4px}} pre{{font:17px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:#0b0f13;border-radius:10px;padding:14px 16px;overflow-x:auto;margin:10px 0}}
pre.wrap{{white-space:pre-wrap;word-break:break-word}} mark{{background:var(--mark);color:#111;border-radius:4px;padding:0 4px;font-weight:700}}
.big{{display:flex;flex-wrap:wrap;gap:12px 28px;font-size:30px;font-weight:700;margin:4px 0 8px}} .ok{{color:var(--ok)}}
.note{{color:var(--dim);font-size:17px;margin:8px 0 0}} table{{border-collapse:collapse;width:100%;font-size:18px;margin-bottom:10px}}
td,th{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left}} th{{color:var(--dim);font-weight:600}} .num{{text-align:right}} .mono{{font-family:ui-monospace,Menlo,monospace}}
</style></head><body><main><h1>Dr Sepsis on IBM LinuxONE</h1>
<p class=sub>Evidence captured on the VM (datathon-test). Built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from evidence/*.txt and demo/secure_summary.json. No tokens, no seeds, no patient data.</p>
{''.join(cards)}</main></body></html>"""
open(sys.argv[1], "w").write(page)
print(f"wrote {sys.argv[1]} ({len(page):,} bytes); allowed-fields PASS={pass_fields}")
