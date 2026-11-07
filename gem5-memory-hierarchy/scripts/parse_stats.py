#!/usr/bin/env python3
"""
Collect metrics from every results/<experiment>/<run>/stats.txt, then write:

  results/summary.csv          one row per run, all metrics
  results/tables/<exp>.md      a Markdown table per experiment (paste into report)
  results/plots/<exp>.png      a two-panel bar chart per experiment

    python3 scripts/parse_stats.py [--results results] [--l1-hit-cycles 2]

Metrics
  CPI                = system.cpu.numCycles / simInsts
  L1D / L1I / L2 hit rate, miss rate (overallMissRate::total)
  L1D avg miss latency (cycles) = overallAvgMissLatency::total / clock period
  L1D AMAT (cycles)  = L1 hit time + L1D miss rate x L1D avg miss latency
  DTLB miss rate     = (rdMisses + wrMisses) / (rdAccesses + wrAccesses)
  DTLB MPKI          = DTLB misses per 1000 instructions

Both the current camelCase stat names (gem5 v21+) and the older snake_case
names are recognised.
"""

import argparse
import csv
import glob
import json
import math
import os
import re

STAT_RE = re.compile(r"^(\S+)\s+([-+0-9.eEnaif]+)")

ALIASES = {
    "simInsts": ["simInsts", "sim_insts"],
    "simSeconds": ["simSeconds", "sim_seconds"],
    "simTicks": ["simTicks", "sim_ticks"],
    "numCycles": ["system.cpu.numCycles"],
    "clock": ["system.clk_domain.clock"],
}


def cache_keys(prefix):
    out = {}
    for new, old in [("overallHits", "overall_hits"), ("overallMisses", "overall_misses"),
                     ("overallAccesses", "overall_accesses"),
                     ("overallMissRate", "overall_miss_rate"),
                     ("overallAvgMissLatency", "overall_avg_miss_latency")]:
        out[f"{prefix}.{new}"] = [f"{prefix}.{new}::total", f"{prefix}.{old}::total"]
    return out


ALIASES.update({k.replace("system.cpu.dcache", "l1d"): v
                for k, v in cache_keys("system.cpu.dcache").items()})
ALIASES.update({k.replace("system.cpu.icache", "l1i"): v
                for k, v in cache_keys("system.cpu.icache").items()})
ALIASES.update({k.replace("system.l2cache", "l2"): v
                for k, v in cache_keys("system.l2cache").items()})
for tlb, short in [("dtb", "dtlb"), ("itb", "itlb")]:
    for s in ["rdAccesses", "wrAccesses", "rdMisses", "wrMisses"]:
        ALIASES[f"{short}.{s}"] = [f"system.cpu.mmu.{tlb}.{s}", f"system.cpu.{tlb}.{s}"]
ALIASES["pf.issued"] = ["system.cpu.dcache.prefetcher.pfIssued"]
ALIASES["pf.useful"] = ["system.cpu.dcache.prefetcher.pfUseful"]


def read_stats(path):
    """Return {stat_name: float} for the FIRST stats dump in the file."""
    stats, started = {}, False
    with open(path) as f:
        for line in f:
            if "Begin Simulation Statistics" in line:
                if started:
                    break
                started = True
                continue
            if "End Simulation Statistics" in line:
                break
            m = STAT_RE.match(line)
            if m:
                try:
                    stats[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
    return stats


def get(stats, key, default=float("nan")):
    for name in ALIASES.get(key, [key]):
        if name in stats:
            return stats[name]
    return default


def div(a, b):
    return a / b if b and not math.isnan(a) and not math.isnan(b) else float("nan")


def metrics(stats, hit_cycles):
    m = {}
    insts = get(stats, "simInsts")
    period = get(stats, "clock", 500.0)  # ticks per cycle (500 at 2 GHz)
    m["sim_insts"] = insts
    m["sim_seconds"] = get(stats, "simSeconds")
    m["cycles"] = get(stats, "numCycles")
    m["CPI"] = div(m["cycles"], insts)
    for c in ["l1d", "l1i", "l2"]:
        acc = get(stats, f"{c}.overallAccesses")
        hits = get(stats, f"{c}.overallHits", 0.0)
        miss = get(stats, f"{c}.overallMisses", 0.0)
        mr = get(stats, f"{c}.overallMissRate", div(miss, acc))
        m[f"{c}_accesses"] = acc
        m[f"{c}_misses"] = miss
        m[f"{c}_hit_rate_pct"] = 100 * div(hits, acc)
        m[f"{c}_miss_rate_pct"] = 100 * mr
        m[f"{c}_mpki"] = 1000 * div(miss, insts)
        m[f"{c}_avg_miss_lat_cyc"] = div(get(stats, f"{c}.overallAvgMissLatency"), period)
    m["l1d_amat_cyc"] = hit_cycles + (m["l1d_miss_rate_pct"] / 100) * m["l1d_avg_miss_lat_cyc"]
    for t in ["dtlb", "itlb"]:
        acc = get(stats, f"{t}.rdAccesses", 0.0) + get(stats, f"{t}.wrAccesses", 0.0)
        miss = get(stats, f"{t}.rdMisses", 0.0) + get(stats, f"{t}.wrMisses", 0.0)
        m[f"{t}_accesses"] = acc
        m[f"{t}_misses"] = miss
        m[f"{t}_miss_rate_pct"] = 100 * div(miss, acc)
        m[f"{t}_mpki"] = 1000 * div(miss, insts)
    m["pf_issued"] = get(stats, "pf.issued")
    m["pf_useful"] = get(stats, "pf.useful")
    return m


TABLE_COLS = {
    "default": [("CPI", "CPI"), ("l1d_hit_rate_pct", "L1D hit %"),
                ("l1d_miss_rate_pct", "L1D miss %"), ("l1d_mpki", "L1D MPKI"),
                ("l2_miss_rate_pct", "L2 miss %"), ("l1d_avg_miss_lat_cyc", "L1D miss lat (cyc)"),
                ("l1d_amat_cyc", "L1D AMAT (cyc)"), ("sim_seconds", "Sim time (s)")],
    "tlb": [("CPI", "CPI"), ("dtlb_accesses", "DTLB accesses"), ("dtlb_misses", "DTLB misses"),
            ("dtlb_miss_rate_pct", "DTLB miss %"), ("dtlb_mpki", "DTLB MPKI"),
            ("l1d_miss_rate_pct", "L1D miss %"), ("sim_seconds", "Sim time (s)")],
}
TLB_EXPS = {"dtlb_size", "dtlb_matmul", "page_locality"}
PLOT_PANELS = {
    "default": [("l1d_miss_rate_pct", "L1D miss rate (%)"), ("CPI", "Cycles per instruction")],
    "tlb": [("dtlb_miss_rate_pct", "DTLB miss rate (%)"), ("CPI", "Cycles per instruction")],
}


def fmt(v):
    if isinstance(v, float):
        if math.isnan(v):
            return "-"
        if abs(v) >= 1e5:
            return f"{v:,.0f}"
        return f"{v:.4f}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def sort_key(run):
    nums = re.findall(r"[\d.]+", run["run"])
    scale = 1024 if "MB" in run["run"] else 1
    return (float(nums[0]) * scale if nums else float("inf"), run["run"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results"))
    ap.add_argument("--l1-hit-cycles", type=float, default=2.0,
                    help="L1 hit time used in the AMAT formula (tag_latency in cache_config.py)")
    ap.add_argument("--no-plots", action="store_true")
    a = ap.parse_args()

    rows = []
    for stats_path in sorted(glob.glob(os.path.join(a.results, "*", "*", "stats.txt"))):
        d = os.path.dirname(stats_path)
        pfile = os.path.join(d, "params.json")
        params = json.load(open(pfile)) if os.path.exists(pfile) else {
            "experiment": os.path.basename(os.path.dirname(d)), "run": os.path.basename(d)}
        stats = read_stats(stats_path)
        if not stats:
            print(f"warning: no statistics in {stats_path} (did the run finish?)")
            continue
        rows.append({"experiment": params["experiment"], "run": params["run"],
                     "benchmark": params.get("benchmark", ""),
                     **metrics(stats, a.l1_hit_cycles)})
    if not rows:
        raise SystemExit(f"no stats.txt files found under {a.results}")

    os.makedirs(os.path.join(a.results, "tables"), exist_ok=True)
    with open(os.path.join(a.results, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    by_exp = {}
    for r in rows:
        by_exp.setdefault(r["experiment"], []).append(r)

    for exp, runs in by_exp.items():
        runs.sort(key=sort_key)
        kind = "tlb" if exp in TLB_EXPS else "default"
        cols = TABLE_COLS[kind]
        if exp == "prefetch":
            cols = cols + [("pf_issued", "Prefetches issued"), ("pf_useful", "Useful prefetches")]
        lines = [f"**{exp}** (benchmark: `{runs[0]['benchmark']}`)", "",
                 "| Run | " + " | ".join(h for _, h in cols) + " |",
                 "| --- |" + " --- |" * len(cols)]
        for r in runs:
            lines.append(f"| {r['run']} | " + " | ".join(fmt(r[k]) for k, _ in cols) + " |")
        md = "\n".join(lines) + "\n"
        with open(os.path.join(a.results, "tables", f"{exp}.md"), "w") as f:
            f.write(md)
        print(md)

    if a.no_plots:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - skipping plots (pip install matplotlib)")
        return

    os.makedirs(os.path.join(a.results, "plots"), exist_ok=True)
    for exp, runs in by_exp.items():
        if len(runs) < 2:
            continue
        kind = "tlb" if exp in TLB_EXPS else "default"
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
        labels = [r["run"] for r in runs]
        for ax, (key, title) in zip(axes, PLOT_PANELS[kind]):
            vals = [r[key] for r in runs]
            bars = ax.bar(labels, vals, color="#2f6db5", width=0.6)
            for b, v in zip(bars, vals):
                if not math.isnan(v):
                    ax.annotate(fmt(v), (b.get_x() + b.get_width() / 2, v),
                                ha="center", va="bottom", fontsize=8,
                                xytext=(0, 2), textcoords="offset points")
            ax.set_title(title, fontsize=11)
            ax.spines[["top", "right"]].set_visible(False)
            ax.tick_params(axis="x", labelsize=9)
            ax.set_ylim(0, max([v for v in vals if not math.isnan(v)] + [1e-9]) * 1.15)
        fig.suptitle(f"{exp}  ({runs[0]['benchmark']})", fontsize=12)
        fig.tight_layout()
        out = os.path.join(a.results, "plots", f"{exp}.png")
        fig.savefig(out, dpi=150)
        plt.close(fig)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
