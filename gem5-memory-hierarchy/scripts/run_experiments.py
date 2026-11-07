#!/usr/bin/env python3
"""
Run every Assignment 3 experiment in gem5 and store the outputs under results/.

    python3 scripts/run_experiments.py --gem5 ~/gem5/build/X86/gem5.opt -j 4
    python3 scripts/run_experiments.py --only baseline l1d_size     # subset
    python3 scripts/run_experiments.py --list                       # show runs

Each run gets its own directory results/<experiment>/<run>/ containing gem5's
stats.txt, config.ini, config.json, run.log (stdout+stderr) and params.json
(the parameter that was swept, used by parse_stats.py for tables and graphs).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "configs", "cache_config.py")
MEM_BENCH = os.path.join(ROOT, "benchmarks", "mem_bench")
HELLO = os.path.join(ROOT, "benchmarks", "hello")

# Main cache workload: 128x128 double matmul = 3 x 128 KB matrices (~384 KB)
MATMUL = "matmul 128"
# TLB workload: touch one byte per 4 KB page over 2 MB (512 pages), 50 passes
TLB_WALK = "stride 2048 4096 50"


def experiments():
    """Return a list of (experiment, run_name, binary, bench_options, gem5_flags, swept)."""
    runs = []

    def add(exp, name, flags, swept, bench=MATMUL, binary=MEM_BENCH):
        runs.append((exp, name, binary, bench, flags, swept))

    # 0. Hello World sanity check
    add("hello", "hello", [], {"run": "hello"}, bench="", binary=HELLO)

    # 1. Baseline: gem5 default caches (L1I 32kB/2, L1D 64kB/2, L2 2MB/8, 64 B)
    add("baseline", "default", [], {"config": "default"})

    # 2. L1D size
    for s in ["8kB", "16kB", "32kB", "64kB", "128kB"]:
        add("l1d_size", s, [f"--l1d-size={s}"], {"l1d_size": s})

    # 3. L1D associativity
    for a in [1, 2, 4, 8, 16]:
        add("l1d_assoc", f"{a}way", [f"--l1d-assoc={a}"], {"l1d_assoc": a})

    # 4. Block (cache line) size
    for b in [16, 32, 64, 128, 256]:
        add("block_size", f"{b}B", [f"--block-size={b}"], {"block_size": b})

    # 5. L2 size (the working set is ~384 KB, so 128-512 kB is the interesting range)
    for s in ["128kB", "256kB", "512kB", "1MB", "2MB"]:
        add("l2_size", s, [f"--l2-size={s}"], {"l2_size": s})

    # 6. Prefetching at L1D (advanced optimisation)
    for p in ["none", "tagged", "stride"]:
        add("prefetch", p, [f"--l1d-prefetcher={p}"], {"l1d_prefetcher": p})

    # 7. Replacement policy (small L1D so that replacement decisions matter)
    for r in ["lru", "fifo", "random", "brrip"]:
        add("replacement", r, ["--l1d-size=16kB", f"--repl={r}"], {"replacement": r})

    # 8. Data-TLB size with a page-stride walk (512 distinct pages)
    for t in [8, 16, 32, 64, 128, 256, 512, 1024]:
        add("dtlb_size", f"{t}e", [f"--dtlb-entries={t}"], {"dtlb_entries": t}, bench=TLB_WALK)

    # 9. Data-TLB size with the matmul workload (~96 data pages)
    for t in [8, 16, 32, 64, 128]:
        add("dtlb_matmul", f"{t}e", [f"--dtlb-entries={t}"], {"dtlb_entries": t})

    # 10. Page-granularity locality: same 2 MB array, different strides.
    #     Accesses per page = 4096 / stride, so this emulates how a larger page
    #     size (more useful data per translation) lowers the TLB miss rate.
    for st in [64, 256, 1024, 4096]:
        add("page_locality", f"stride{st}", ["--dtlb-entries=64"], {"stride_bytes": st},
            bench=f"stride 2048 {st} 4")

    return runs


def run_one(gem5, outroot, run, dry):
    exp, name, binary, bench, flags, swept = run
    outdir = os.path.join(outroot, exp, name)
    os.makedirs(outdir, exist_ok=True)
    cmd = [gem5, f"--outdir={outdir}", CONFIG, binary]
    if bench:
        cmd.append(f"--options={bench}")
    cmd += flags
    with open(os.path.join(outdir, "params.json"), "w") as f:
        json.dump({"experiment": exp, "run": name, "benchmark": bench or "hello",
                   "flags": flags, "swept": swept, "command": " ".join(cmd)}, f, indent=2)
    if dry:
        return exp, name, 0, 0.0, " ".join(cmd)
    t0 = time.time()
    with open(os.path.join(outdir, "run.log"), "w") as log:
        rc = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT)
    return exp, name, rc, time.time() - t0, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gem5", default=os.path.expanduser("~/gem5/build/X86/gem5.opt"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results"))
    ap.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--only", nargs="*", help="experiment names to run")
    ap.add_argument("--list", action="store_true", help="print the run list and exit")
    ap.add_argument("--dry-run", action="store_true", help="write params.json and print commands only")
    a = ap.parse_args()

    runs = [r for r in experiments() if not a.only or r[0] in a.only]
    if a.list:
        for r in runs:
            print(f"{r[0]:14s} {r[1]:10s} {r[3] or 'hello':24s} {' '.join(r[4])}")
        return
    if not a.dry_run:
        for path in [a.gem5, MEM_BENCH, HELLO]:
            if not os.path.exists(path):
                sys.exit(f"missing {path} - build gem5 and run 'make -C benchmarks' first")

    print(f"Running {len(runs)} simulations with {a.jobs} parallel jobs ...")
    failed = 0
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futs = [pool.submit(run_one, a.gem5, a.out, r, a.dry_run) for r in runs]
        for fut in as_completed(futs):
            exp, name, rc, secs, cmd = fut.result()
            if a.dry_run:
                print(cmd)
                continue
            status = "ok" if rc == 0 else f"FAILED (rc={rc}, see run.log)"
            failed += rc != 0
            print(f"  {exp:14s} {name:10s} {secs:7.1f}s  {status}")
    if failed:
        sys.exit(f"{failed} run(s) failed")
    print("Done. Next: python3 scripts/parse_stats.py")


if __name__ == "__main__":
    main()
