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
         f"Run in the Jupyter image on the VM, reported as is. The accelerator is visible; our logistic-regression model "
         f"does not use it. {esc(captured(torch))}"),
    card("Container hardening + mutual TLS", f"<pre>{esc(body(hard))}</pre>",
         "From docker inspect on the live containers: non-root 1000:1000, read-only root filesystem, all capabilities dropped, "
         "no-new-privileges, pids limit, demo ports only on 127.0.0.1. Hospital-to-aggregator traffic is mutual TLS 1.3 "
         "(client certificate required, CN = site). On-chip crypto (CPACF) use is NOT claimed: it could not be confirmed. "
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
page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SepsisBridge Evidence Board</title><style>
:root{{--bg:#0f1419;--card:#182029;--ink:#e8edf2;--dim:#9aa7b4;--ok:#5fd38d;--mark:#ffd166;--line:#2a3542}}
body{{margin:0;background:var(--bg);color:var(--ink);font:20px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 60px}} h1{{font-size:38px;margin:0 0 4px}}
.sub{{color:var(--dim);margin:0 0 24px}} section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:0 0 22px}}
h2{{font-size:26px;margin:0 0 14px}} pre{{font:17px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:#0b0f13;border-radius:10px;padding:14px 16px;overflow-x:auto;margin:10px 0}}
pre.wrap{{white-space:pre-wrap;word-break:break-word}} mark{{background:var(--mark);color:#111;border-radius:4px;padding:0 4px;font-weight:700}}
.big{{display:flex;flex-wrap:wrap;gap:12px 28px;font-size:30px;font-weight:700;margin:4px 0 8px}} .ok{{color:var(--ok)}}
.note{{color:var(--dim);font-size:17px;margin:8px 0 0}} table{{border-collapse:collapse;width:100%;font-size:18px;margin-bottom:10px}}
td,th{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left}} th{{color:var(--dim);font-weight:600}} .num{{text-align:right}} .mono{{font-family:ui-monospace,Menlo,monospace}}
</style></head><body><main><h1>SepsisBridge on IBM LinuxONE</h1>
<p class=sub>Evidence captured on the VM (datathon-test). Built {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from evidence/*.txt and demo/secure_summary.json. No tokens, no seeds, no patient data.</p>
{''.join(cards)}</main></body></html>"""
open(sys.argv[1], "w").write(page)
print(f"wrote {sys.argv[1]} ({len(page):,} bytes); allowed-fields PASS={pass_fields}")
