"""
Assignment 3 - Exploring Memory Hierarchy Design in gem5
Configurable x86 syscall-emulation (SE) system with a two-level classic cache
hierarchy and configurable TLBs.

Usage (from the gem5 source directory):
    build/X86/gem5.opt --outdir=results/baseline \
        /path/to/configs/cache_config.py /path/to/benchmarks/mem_bench \
        --options="matmul 128"

Every cache / TLB parameter below can be changed from the command line, so
that one script drives all experiments.  Defaults match the defaults used by
gem5's own configs/common/Options.py (se.py), so running with no cache flags
is the "default cache configuration" baseline.

Tested against the gem5 v23/v24 Python API (port names cpu_side_ports /
mem_side_ports, MemCtrl + DRAM interface, X86TimingSimpleCPU, cpu.mmu.dtb).
"""

import argparse

import m5
from m5.objects import *

# --------------------------------------------------------------------------
# Command-line options
# --------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Configurable x86 cache/TLB system")
parser.add_argument("binary", help="statically linked x86 binary to run")
parser.add_argument("--options", default="", help="arguments passed to the binary")

# CPU / system
parser.add_argument("--cpu", choices=["timing", "minor", "o3"], default="timing",
                    help="CPU model (timing = TimingSimpleCPU, in-order, 1 IPC max)")
parser.add_argument("--clock", default="2GHz")
parser.add_argument("--mem-size", default="512MB")

# Cache parameters (defaults = gem5 Options.py defaults)
parser.add_argument("--l1i-size", default="32kB")
parser.add_argument("--l1d-size", default="64kB")
parser.add_argument("--l1i-assoc", type=int, default=2)
parser.add_argument("--l1d-assoc", type=int, default=2)
parser.add_argument("--l2-size", default="2MB")
parser.add_argument("--l2-assoc", type=int, default=8)
parser.add_argument("--no-l2", action="store_true", help="remove the L2 cache")
parser.add_argument("--block-size", type=int, default=64,
                    help="cache line size in bytes (system-wide in gem5)")

# Advanced optimisations
parser.add_argument("--l1d-prefetcher", choices=["none", "stride", "tagged"], default="none")
parser.add_argument("--l2-prefetcher", choices=["none", "stride", "tagged"], default="none")
parser.add_argument("--prefetch-degree", type=int, default=4)
parser.add_argument("--repl", choices=["lru", "random", "fifo", "brrip"], default="lru",
                    help="replacement policy for all caches")

# Virtual memory / TLB
parser.add_argument("--dtlb-entries", type=int, default=64)
parser.add_argument("--itlb-entries", type=int, default=64)

args = parser.parse_args()


# --------------------------------------------------------------------------
# Cache classes
# --------------------------------------------------------------------------
def make_repl():
    return {"lru": LRURP, "random": RandomRP, "fifo": FIFORP, "brrip": BRRIPRP}[args.repl]()


def make_prefetcher(kind):
    if kind == "stride":
        return StridePrefetcher(degree=args.prefetch_degree)
    if kind == "tagged":
        return TaggedPrefetcher(degree=args.prefetch_degree)
    return NULL


class L1Cache(Cache):
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 4
    tgts_per_mshr = 20


class L1ICache(L1Cache):
    pass


class L1DCache(L1Cache):
    pass


class L2Cache(Cache):
    tag_latency = 20
    data_latency = 20
    response_latency = 20
    mshrs = 20
    tgts_per_mshr = 12


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------
system = System()
system.clk_domain = SrcClockDomain(clock=args.clock, voltage_domain=VoltageDomain())
system.mem_mode = "timing"
system.mem_ranges = [AddrRange(args.mem_size)]
system.cache_line_size = args.block_size

cpu_class = {"timing": X86TimingSimpleCPU, "minor": X86MinorCPU, "o3": X86O3CPU}[args.cpu]
system.cpu = cpu_class()

# TLB configuration (x86 TLBs live inside the CPU's MMU)
system.cpu.mmu.dtb.size = args.dtlb_entries
system.cpu.mmu.itb.size = args.itlb_entries

# L1 caches
system.cpu.icache = L1ICache(size=args.l1i_size, assoc=args.l1i_assoc,
                             replacement_policy=make_repl())
system.cpu.dcache = L1DCache(size=args.l1d_size, assoc=args.l1d_assoc,
                             replacement_policy=make_repl(),
                             prefetcher=make_prefetcher(args.l1d_prefetcher))
system.cpu.icache_port = system.cpu.icache.cpu_side
system.cpu.dcache_port = system.cpu.dcache.cpu_side

system.membus = SystemXBar()

if args.no_l2:
    system.cpu.icache.mem_side = system.membus.cpu_side_ports
    system.cpu.dcache.mem_side = system.membus.cpu_side_ports
else:
    system.l2bus = L2XBar()
    system.cpu.icache.mem_side = system.l2bus.cpu_side_ports
    system.cpu.dcache.mem_side = system.l2bus.cpu_side_ports
    system.l2cache = L2Cache(size=args.l2_size, assoc=args.l2_assoc,
                             replacement_policy=make_repl(),
                             prefetcher=make_prefetcher(args.l2_prefetcher))
    system.l2cache.cpu_side = system.l2bus.mem_side_ports
    system.l2cache.mem_side = system.membus.cpu_side_ports

# x86 interrupt controller must be wired to the memory bus
system.cpu.createInterruptController()
system.cpu.interrupts[0].pio = system.membus.mem_side_ports
system.cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
system.cpu.interrupts[0].int_responder = system.membus.mem_side_ports

system.system_port = system.membus.cpu_side_ports

# Main memory
system.mem_ctrl = MemCtrl()
system.mem_ctrl.dram = DDR3_1600_8x8()
system.mem_ctrl.dram.range = system.mem_ranges[0]
system.mem_ctrl.port = system.membus.mem_side_ports

# --------------------------------------------------------------------------
# Workload (syscall emulation)
# --------------------------------------------------------------------------
system.workload = SEWorkload.init_compatible(args.binary)
process = Process()
process.cmd = [args.binary] + args.options.split()
system.cpu.workload = process
system.cpu.createThreads()

root = Root(full_system=False, system=system)
m5.instantiate()

print("=== Assignment 3 configuration ===")
for k, v in sorted(vars(args).items()):
    print(f"  {k:16s} = {v}")
print("Beginning simulation!")
exit_event = m5.simulate()
print(f"Exiting @ tick {m5.curTick()} because {exit_event.getCause()}")
