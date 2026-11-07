/*
 * mem_bench.c - small memory-hierarchy micro-benchmarks for gem5 SE mode.
 *
 * Build (must be static for gem5 syscall emulation):
 *     gcc -O2 -static -o mem_bench mem_bench.c
 *
 * Modes:
 *   mem_bench matmul N
 *       Naive i-j-k double-precision matrix multiply (N x N).  Row-wise access
 *       to A, column-wise (stride N*8 bytes) access to B: sensitive to cache
 *       size, associativity and block size.
 *
 *   mem_bench stride SIZE_KB STRIDE_BYTES PASSES
 *       Repeatedly walk an array of SIZE_KB with the given stride.  With a
 *       4096-byte stride every access touches a new 4 KB page, so the number of
 *       distinct pages = SIZE_KB/4 and TLB behaviour dominates.
 *
 *   mem_bench chase SIZE_KB STEPS
 *       Random pointer chase through SIZE_KB of memory (poor locality,
 *       defeats prefetchers).
 *
 * Add "huge" as the last argument (host runs only) to request transparent huge
 * pages with madvise(MADV_HUGEPAGE); gem5 SE mode ignores this call.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>

static int want_huge = 0;

static void *alloc(size_t bytes)
{
    void *p = NULL;
    size_t align = want_huge ? (2u << 20) : 4096;
    size_t rounded = (bytes + align - 1) / align * align;
    if (posix_memalign(&p, align, rounded) != 0) {
        fprintf(stderr, "allocation of %zu bytes failed\n", rounded);
        exit(1);
    }
#ifdef MADV_HUGEPAGE
    if (want_huge)
        madvise(p, rounded, MADV_HUGEPAGE);
#endif
    memset(p, 0, rounded); /* touch every page once (first-touch page faults) */
    return p;
}

static double matmul(int n)
{
    double *a = alloc(sizeof(double) * n * n);
    double *b = alloc(sizeof(double) * n * n);
    double *c = alloc(sizeof(double) * n * n);
    for (int i = 0; i < n * n; i++) {
        a[i] = (double)(i % 7);
        b[i] = (double)(i % 5);
    }
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++) {
            double sum = 0.0;
            for (int k = 0; k < n; k++)
                sum += a[i * n + k] * b[k * n + j];
            c[i * n + j] = sum;
        }
    double check = 0.0;
    for (int i = 0; i < n * n; i++)
        check += c[i];
    return check;
}

static double stride_walk(size_t size_kb, size_t stride, int passes)
{
    size_t bytes = size_kb * 1024;
    volatile char *buf = alloc(bytes);
    uint64_t sum = 0;
    for (int p = 0; p < passes; p++)
        for (size_t off = 0; off < bytes; off += stride) {
            buf[off] += 1;
            sum += buf[off];
        }
    return (double)sum;
}

static double chase(size_t size_kb, long steps)
{
    size_t n = size_kb * 1024 / sizeof(size_t);
    size_t *next = alloc(n * sizeof(size_t));
    /* Sattolo's algorithm: one random cycle through all elements */
    for (size_t i = 0; i < n; i++)
        next[i] = i;
    uint64_t seed = 12345;
    for (size_t i = n - 1; i > 0; i--) {
        seed = seed * 6364136223846793005ULL + 1442695040888963407ULL;
        size_t j = (size_t)(seed >> 33) % i;
        size_t t = next[i]; next[i] = next[j]; next[j] = t;
    }
    size_t idx = 0;
    for (long s = 0; s < steps; s++)
        idx = next[idx];
    return (double)idx;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: mem_bench matmul N\n"
            "       mem_bench stride SIZE_KB STRIDE_BYTES PASSES\n"
            "       mem_bench chase SIZE_KB STEPS\n"
            "       (append 'huge' to request huge pages on a real host)\n");
    exit(1);
}

int main(int argc, char **argv)
{
    if (argc < 2)
        usage();
    if (strcmp(argv[argc - 1], "huge") == 0) {
        want_huge = 1;
        argc--;
    }
    double r;
    if (strcmp(argv[1], "matmul") == 0 && argc == 3) {
        r = matmul(atoi(argv[2]));
    } else if (strcmp(argv[1], "stride") == 0 && argc == 5) {
        r = stride_walk(strtoul(argv[2], 0, 10), strtoul(argv[3], 0, 10), atoi(argv[4]));
    } else if (strcmp(argv[1], "chase") == 0 && argc == 4) {
        r = chase(strtoul(argv[2], 0, 10), atol(argv[3]));
    } else {
        usage();
        return 1;
    }
    printf("mem_bench %s done, checksum = %.1f\n", argv[1], r);
    return 0;
}
