#ifndef NUMBA_NRT_EXTERNAL_H_
#define NUMBA_NRT_EXTERNAL_H_

#include <stdlib.h>

typedef struct MemInfo NRT_MemInfo;

typedef void NRT_managed_dtor(void *data);


typedef struct {
    /* Methods to create MemInfos.

    MemInfos are like smart pointers for objects that are managed by the Numba.
    */

    /* Allocate memory *nbytes*.
    */
    NRT_MemInfo* (*allocate)(size_t nbytes);

    /* Convert externally allocated memory into a MemInfo.

    *dtor* is the deallocator of the memory
    */
    NRT_MemInfo* (*manage_memory)(void *data, NRT_managed_dtor dtor);

    /* Acquire a reference */
    void (*acquire)(NRT_MemInfo* mi);

    /* Release a reference */
    void (*release)(NRT_MemInfo* mi);
} NRT_Functions;



#endif /* NUMBA_NRT_EXTERNAL_H_ */
