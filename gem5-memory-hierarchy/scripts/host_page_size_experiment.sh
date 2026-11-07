#!/usr/bin/env bash
# Optional real-hardware page-size experiment (Linux host, not gem5).
#
# gem5's x86 SE mode uses a fixed 4 KB page, so this script complements the
# simulation by running the same benchmark natively with 4 KB pages and with
# 2 MB transparent huge pages (THP), and reporting page faults and dTLB misses.
#
#   bash scripts/host_page_size_experiment.sh > results/host_page_size.txt
#
# Needs: perf (linux-tools) for TLB counters; falls back to /usr/bin/time -v.
# THP must be "madvise" or "always": cat /sys/kernel/mm/transparent_hugepage/enabled
set -u
cd "$(dirname "$0")/.."
BIN=benchmarks/mem_bench
[ -x "$BIN" ] || make -C benchmarks mem_bench

echo "THP setting: $(cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo unknown)"
for workload in "stride 262144 4096 20" "chase 262144 20000000" "matmul 512"; do
  for mode in 4k huge; do
    extra=""; [ "$mode" = huge ] && extra="huge"
    echo; echo "=== $workload  [$mode pages] ==="
    if command -v perf >/dev/null 2>&1; then
      perf stat -e page-faults,dTLB-loads,dTLB-load-misses,cycles,instructions \
        $BIN $workload $extra 2>&1 | grep -Ev '^\s*$'
    else
      /usr/bin/time -v $BIN $workload $extra 2>&1 | \
        grep -E 'checksum|Elapsed|page faults|Maximum resident'
    fi
  done
done
