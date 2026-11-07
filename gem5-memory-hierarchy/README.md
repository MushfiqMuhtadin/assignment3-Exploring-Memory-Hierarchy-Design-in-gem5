# Assignment 3 – Exploring Memory Hierarchy Design in gem5

Course: [Course] · Student: [Your Name] · Instructor: [Professor]

This repository holds everything for Part 2: the gem5 configuration, the
benchmark programs, the scripts that run every experiment and turn gem5's
`stats.txt` into tables and graphs, and the raw results.

```
configs/cache_config.py              x86 SE system: L1I/L1D/L2 + TLBs, all parameters on the command line
benchmarks/hello.c                   "Hello World" sanity check
benchmarks/mem_bench.c               matmul / page-stride / pointer-chase micro-benchmarks
benchmarks/Makefile                  builds both as static binaries (required by gem5 SE mode)
scripts/run_experiments.py           runs all 46 simulations (parallel) into results/<experiment>/<run>/
scripts/parse_stats.py               results/summary.csv, results/tables/*.md, results/plots/*.png
scripts/host_page_size_experiment.sh optional native 4 KB vs 2 MB page comparison with perf
results/                             gem5 outputs (stats.txt, config.ini, run.log) – committed
screenshots/                         screenshots used in the report
```

## 1. Build gem5 (Ubuntu 22.04 / 24.04)

```bash
sudo apt update
sudo apt install -y build-essential git m4 scons zlib1g zlib1g-dev \
    libprotobuf-dev protobuf-compiler libprotoc-dev libgoogle-perftools-dev \
    python3-dev python3-pip libboost-all-dev pkg-config libhdf5-serial-dev \
    libpng-dev libelf-dev python3-matplotlib
git clone https://github.com/gem5/gem5.git ~/gem5
cd ~/gem5
scons build/X86/gem5.opt -j"$(nproc)"     # 30-90 min; use -j2 if you run out of RAM
build/X86/gem5.opt --version              # screenshot this
```

## 2. Build the benchmarks

```bash
git clone <this repo> ~/gem5-memory-hierarchy && cd ~/gem5-memory-hierarchy
make -C benchmarks
./benchmarks/hello && ./benchmarks/mem_bench matmul 128   # native check
```

## 3. Hello World in gem5 (graded item)

```bash
~/gem5/build/X86/gem5.opt --outdir=results/hello/hello \
    configs/cache_config.py benchmarks/hello
```
Expected tail of the output: `Hello world! (from gem5 x86 SE mode)` followed by
`Exiting @ tick ... because exiting with last active thread context`.
gem5's own test binary works too, if your checkout has it:
`configs/cache_config.py ~/gem5/tests/test-progs/hello/bin/x86/linux/hello`.

## 4. Baseline and all experiments

```bash
python3 scripts/run_experiments.py --list                         # what will run
python3 scripts/run_experiments.py --gem5 ~/gem5/build/X86/gem5.opt -j 4
python3 scripts/parse_stats.py                                    # tables + plots
```
Run one experiment at a time with `--only baseline l1d_size`.

| Experiment | Swept parameter | Values | Workload |
| --- | --- | --- | --- |
| baseline | none (gem5 defaults) | L1I 32kB/2-way, L1D 64kB/2-way, L2 2MB/8-way, 64 B lines, 64-entry TLBs | matmul 128 |
| l1d_size | L1D capacity | 8, 16, 32, 64, 128 kB | matmul 128 |
| l1d_assoc | L1D ways | 1, 2, 4, 8, 16 | matmul 128 |
| block_size | line size | 16, 32, 64, 128, 256 B | matmul 128 |
| l2_size | L2 capacity | 128 kB – 2 MB | matmul 128 |
| prefetch | L1D prefetcher | none, tagged (next-line), stride | matmul 128 |
| replacement | policy (16 kB L1D) | LRU, FIFO, Random, BRRIP | matmul 128 |
| dtlb_size | DTLB entries | 8 – 1024 | stride 2048 KB / 4096 B / 50 passes (512 pages) |
| dtlb_matmul | DTLB entries | 8 – 128 | matmul 128 |
| page_locality | access stride | 64, 256, 1024, 4096 B | stride over 2 MB |

A single run by hand, for example a 4-way 32 kB L1D with a stride prefetcher:
```bash
~/gem5/build/X86/gem5.opt --outdir=results/manual/test configs/cache_config.py \
    benchmarks/mem_bench --options="matmul 128" \
    --l1d-size=32kB --l1d-assoc=4 --l1d-prefetcher=stride
```
`python3 configs/...` will not work – the script must be launched by gem5.opt.

## 5. Where the metrics come from (stats.txt)

| Metric | gem5 statistic(s) |
| --- | --- |
| L1D hit / miss rate | `system.cpu.dcache.overallHits::total`, `overallMisses::total`, `overallMissRate::total` |
| L2 miss rate | `system.l2cache.overallMissRate::total` |
| Avg. miss latency | `system.cpu.dcache.overallAvgMissLatency::total` (ticks; ÷ `system.clk_domain.clock`) |
| AMAT | 2-cycle hit + miss rate × avg. miss latency (computed by parse_stats.py) |
| CPI | `system.cpu.numCycles / simInsts` |
| DTLB miss rate | `system.cpu.mmu.dtb.{rd,wr}Misses / {rd,wr}Accesses` |

## 6. Page size note

gem5's x86 SE mode uses a fixed 4 KB page (`PageShift = 12` in the x86 ISA
source, `src/arch/x86/page_size.hh` in recent releases), and SE mode does not
model page-walk latency or demand-paging faults the way an OS does. The
`page_locality` experiment and TLB-reach analysis stand in for page size in
simulation; `scripts/host_page_size_experiment.sh` measures real 4 KB vs 2 MB
pages on the host with `perf`. Changing `PageShift` and rebuilding gem5 is
possible but optional and slow.

## 7. Push to a private GitHub repository

```bash
git init && git add . && git commit -m "Assignment 3: gem5 memory hierarchy"
gh repo create gem5-memory-hierarchy --private --source=. --push
# give only your professor access:
gh api -X PUT repos/<your-user>/gem5-memory-hierarchy/collaborators/<professor-github-user>
```
(or GitHub → Settings → Collaborators → Add people.)
