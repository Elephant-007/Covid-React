/*
 * Copyright © 2017 Intel Corporation
 * SPDX-License-Identifier: BSD-2-Clause
 */

#ifndef GUFUNC_SCHEDULER
#define GUFUNC_SCHEDULER

#include <stdint.h>
#if __SIZEOF_POINTER__ == 8
    #define intp int64_t
    #define uintp uint64_t
#else
    #define intp int
    #define uintp unsigned
#endif

#ifdef __cplusplus
extern "C"
{
#endif

void do_scheduling(intp num_dim, intp *dims, uintp num_threads, intp *sched, intp debug);

#ifdef __cplusplus
}
#endif

#endif
