CC      ?= gcc
CFLAGS  ?= -O2 -static -Wall

all: hello mem_bench

hello: hello.c
	$(CC) $(CFLAGS) -o $@ $<

mem_bench: mem_bench.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f hello mem_bench

.PHONY: all clean
