/* MSVC C99 doesn't have <stdatomic.h>, else this could be written in easily
 * in C */
#include <atomic>

#ifdef _MSC_VER
#include <inttypes.h>
#endif

#include <stdarg.h>
#include <string.h> /* for memset */
#include "nrt.h"
#include "assert.h"

#if !defined MIN
#define MIN(a, b) ((a) < (b)) ? (a) : (b)
#endif


/* NOTE: if changing the layout, please update numba.core.runtime.atomicops */
extern "C" {
struct MemInfo {
    std::atomic_size_t     refct;
    NRT_dtor_function dtor;
    void              *dtor_info;
    void              *data;
    size_t            size;    /* only used for NRT allocated memory */
    NRT_ExternalAllocator *external_allocator;
};
}


/*
 * Misc helpers.
 */

static void nrt_fatal_error(const char *msg)
{
    fprintf(stderr, "Fatal Numba error: %s\n", msg);
    fflush(stderr); /* it helps in Windows debug build */

#if defined(MS_WINDOWS) && defined(_DEBUG)
    DebugBreak();
#endif
    abort();
}

/*
 * Global resources.
 */
extern "C" {

struct MemSys {
    /* Shutdown flag */
    int shutting;
    /* Stats */
    std::atomic_size_t stats_alloc, stats_free, stats_mi_alloc, stats_mi_free;
    /* System allocation functions */
    struct {
        NRT_malloc_func malloc;
        NRT_realloc_func realloc;
        NRT_free_func free;
    } allocator;
};


/* The Memory System object */
static NRT_MemSys TheMSys;

}

extern "C" void NRT_MemSys_init(void) {
    TheMSys.shutting = 0;
    TheMSys.stats_alloc = 0;
    TheMSys.stats_free = 0;
    TheMSys.stats_mi_alloc = 0;
    TheMSys.stats_mi_free = 0;
    /* Bind to libc allocator */
    TheMSys.allocator.malloc = malloc;
    TheMSys.allocator.realloc = realloc;
    TheMSys.allocator.free = free;
}

extern "C" void NRT_MemSys_shutdown(void) {
    TheMSys.shutting = 1;
}

extern "C" void NRT_MemSys_set_allocator(NRT_malloc_func malloc_func,
                              NRT_realloc_func realloc_func,
                              NRT_free_func free_func)
{
    if ((malloc_func != TheMSys.allocator.malloc ||
         realloc_func != TheMSys.allocator.realloc ||
         free_func != TheMSys.allocator.free) &&
         (TheMSys.stats_alloc != TheMSys.stats_free ||
          TheMSys.stats_mi_alloc != TheMSys.stats_mi_free)) {
        nrt_fatal_error("cannot change allocator while blocks are allocated");
    }
    TheMSys.allocator.malloc = malloc_func;
    TheMSys.allocator.realloc = realloc_func;
    TheMSys.allocator.free = free_func;
}

extern "C" size_t NRT_MemSys_get_stats_alloc() {
    return TheMSys.stats_alloc;
}

extern "C" size_t NRT_MemSys_get_stats_free() {
    return TheMSys.stats_free;
}

extern "C" size_t NRT_MemSys_get_stats_mi_alloc() {
    return TheMSys.stats_mi_alloc;
}

extern "C" size_t NRT_MemSys_get_stats_mi_free() {
    return TheMSys.stats_mi_free;
}

/*
 * The MemInfo structure.
 */

extern "C" void NRT_MemInfo_init(NRT_MemInfo *mi,void *data, size_t size,
                      NRT_dtor_function dtor, void *dtor_info,
                      NRT_ExternalAllocator *external_allocator)
{
    mi->refct.store(1);  /* starts with 1 refct */
    mi->dtor = dtor;
    mi->dtor_info = dtor_info;
    mi->data = data;
    mi->size = size;
    mi->external_allocator = external_allocator;
    NRT_Debug(nrt_debug_print("NRT_MemInfo_init mi=%p external_allocator=%p\n", mi, external_allocator));
    /* Update stats */
    TheMSys.stats_mi_alloc++;
}

NRT_MemInfo *NRT_MemInfo_new(void *data, size_t size,
                             NRT_dtor_function dtor, void *dtor_info)
{
    NRT_MemInfo *mi = (NRT_MemInfo *) NRT_Allocate(sizeof(NRT_MemInfo));
    NRT_Debug(nrt_debug_print("NRT_MemInfo_new mi=%p\n", mi));
    NRT_MemInfo_init(mi, data, size, dtor, dtor_info, NULL);
    return mi;
}

size_t NRT_MemInfo_refcount(NRT_MemInfo *mi) {
    /* Should never returns 0 for a valid MemInfo */
    if (mi && mi->data)
        return mi->refct;
    else{
        return (size_t)-1;
    }
}

static
void nrt_internal_dtor_safe(void *ptr, size_t size, void *info) {
    NRT_Debug(nrt_debug_print("nrt_internal_dtor_safe %p, %p\n", ptr, info));
    /* See NRT_MemInfo_alloc_safe() */
    memset(ptr, 0xDE, MIN(size, 256));
}

static
void *nrt_allocate_meminfo_and_data(size_t size, NRT_MemInfo **mi_out, NRT_ExternalAllocator *allocator) {
    NRT_MemInfo *mi;
    NRT_Debug(nrt_debug_print("nrt_allocate_meminfo_and_data %p\n", allocator));
    void *base = NRT_Allocate_External(sizeof(NRT_MemInfo) + size, allocator);
    mi = (NRT_MemInfo *) base;
    *mi_out = mi;
    return (void*)((char *)base + sizeof(NRT_MemInfo));
}


static
void nrt_internal_custom_dtor_safe(void *ptr, size_t size, void *info) {
    NRT_dtor_function dtor = (NRT_dtor_function)info;
    NRT_Debug(nrt_debug_print("nrt_internal_custom_dtor_safe %p, %p\n",
                              ptr, info));
    if (dtor) {
        dtor(ptr, size, NULL);
    }

    nrt_internal_dtor_safe(ptr, size, NULL);
}


NRT_MemInfo *NRT_MemInfo_alloc(size_t size) {
    NRT_MemInfo *mi;
    void *data = nrt_allocate_meminfo_and_data(size, &mi, NULL);
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc %p\n", data));
    NRT_MemInfo_init(mi, data, size, NULL, NULL, NULL);
    return mi;
}

extern "C" NRT_MemInfo *NRT_MemInfo_alloc_external(size_t size, NRT_ExternalAllocator *allocator) {
    NRT_MemInfo *mi;
    void *data = nrt_allocate_meminfo_and_data(size, &mi, allocator);
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc %p\n", data));
    NRT_MemInfo_init(mi, data, size, NULL, NULL, allocator);
    return mi;
}

extern "C" NRT_MemInfo *NRT_MemInfo_alloc_safe(size_t size) {
    return NRT_MemInfo_alloc_dtor_safe(size, NULL);
}

extern "C" NRT_MemInfo* NRT_MemInfo_alloc_dtor_safe(size_t size, NRT_dtor_function dtor) {
    NRT_MemInfo *mi;
    void *data = nrt_allocate_meminfo_and_data(size, &mi, NULL);
    /* Only fill up a couple cachelines with debug markers, to minimize
       overhead. */
    memset(data, 0xCB, MIN(size, 256));
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc_dtor_safe %p %zu\n", data, size));
    NRT_MemInfo_init(mi, data, size, nrt_internal_custom_dtor_safe, (void*)dtor, NULL);
    return mi;
}


static
void *nrt_allocate_meminfo_and_data_align(size_t size, unsigned align,
                                          NRT_MemInfo **mi, NRT_ExternalAllocator *allocator)
{
    size_t offset, intptr, remainder;
    NRT_Debug(nrt_debug_print("nrt_allocate_meminfo_and_data_align %p\n", allocator));
    void *base = nrt_allocate_meminfo_and_data(size + 2 * align, mi, allocator);
    intptr = (size_t) base;
    /* See if we are aligned */
    remainder = intptr % align;
    if (remainder == 0){ /* Yes */
        offset = 0;
    } else { /* No, move forward `offset` bytes */
        offset = align - remainder;
    }
    return (void*)((char *)base + offset);
}

extern "C" NRT_MemInfo *NRT_MemInfo_alloc_aligned(size_t size, unsigned align) {
    NRT_MemInfo *mi;
    void *data = nrt_allocate_meminfo_and_data_align(size, align, &mi, NULL);
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc_aligned %p\n", data));
    NRT_MemInfo_init(mi, data, size, NULL, NULL, NULL);
    return mi;
}

extern "C" NRT_MemInfo *NRT_MemInfo_alloc_safe_aligned(size_t size, unsigned align) {
    NRT_MemInfo *mi;
    void *data = nrt_allocate_meminfo_and_data_align(size, align, &mi, NULL);
    /* Only fill up a couple cachelines with debug markers, to minimize
       overhead. */
    memset(data, 0xCB, MIN(size, 256));
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc_safe_aligned %p %zu\n",
                              data, size));
    NRT_MemInfo_init(mi, data, size, nrt_internal_dtor_safe, (void*)size, NULL);
    return mi;
}

extern "C" NRT_MemInfo *NRT_MemInfo_alloc_safe_aligned_external(size_t size, unsigned align, NRT_ExternalAllocator *allocator) {
    NRT_MemInfo *mi;
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc_safe_aligned_external %p\n", allocator));
    void *data = nrt_allocate_meminfo_and_data_align(size, align, &mi, allocator);
    /* Only fill up a couple cachelines with debug markers, to minimize
       overhead. */
    memset(data, 0xCB, MIN(size, 256));
    NRT_Debug(nrt_debug_print("NRT_MemInfo_alloc_safe_aligned %p %zu\n",
                              data, size));
    NRT_MemInfo_init(mi, data, size, nrt_internal_dtor_safe, (void*)size, allocator);
    return mi;
}

extern "C" void NRT_dealloc(NRT_MemInfo *mi) {
    NRT_Debug(nrt_debug_print("NRT_dealloc meminfo: %p external_allocator: %p\n", mi, mi->external_allocator));
    if (mi->external_allocator) {
        mi->external_allocator->free(mi, mi->external_allocator->opaque_data);
        TheMSys.stats_free++;;
    } else {
        NRT_Free(mi);
    }
}

extern "C" void NRT_MemInfo_destroy(NRT_MemInfo *mi) {
    NRT_dealloc(mi);
    TheMSys.stats_mi_free++;
}

extern "C" void NRT_MemInfo_acquire(NRT_MemInfo *mi) {
    NRT_Debug(nrt_debug_print("NRT_MemInfo_acquire %p refct=%zu\n", mi,
                                                            mi->refct.load()));
    assert(mi->refct > 0 && "RefCt cannot be zero");
    mi->refct++;
}

extern "C" void NRT_MemInfo_call_dtor(NRT_MemInfo *mi) {
    NRT_Debug(nrt_debug_print("NRT_MemInfo_call_dtor %p\n", mi));
    if (mi->dtor && !TheMSys.shutting)
        /* We have a destructor and the system is not shutting down */
        mi->dtor(mi->data, mi->size, mi->dtor_info);
    /* Clear and release MemInfo */
    NRT_MemInfo_destroy(mi);
}

extern "C" void NRT_MemInfo_release(NRT_MemInfo *mi) {
    NRT_Debug(nrt_debug_print("NRT_MemInfo_release %p refct=%zu\n", mi,
                                                            mi->refct.load()));
    assert (mi->refct > 0 && "RefCt cannot be 0");
    /* RefCt drop to zero */
    if ((--mi->refct) == 0) {
        NRT_MemInfo_call_dtor(mi);
    }
}

extern "C" void* NRT_MemInfo_data(NRT_MemInfo* mi) {
    return mi->data;
}

size_t NRT_MemInfo_size(NRT_MemInfo* mi) {
    return mi->size;
}

extern "C" void * NRT_MemInfo_external_allocator(NRT_MemInfo *mi) {
    NRT_Debug(nrt_debug_print("NRT_MemInfo_external_allocator meminfo: %p external_allocator: %p\n", mi, mi->external_allocator));
    return mi->external_allocator;
}

extern "C" void *NRT_MemInfo_parent(NRT_MemInfo *mi) {
    return mi->dtor_info;
}

extern "C" void NRT_MemInfo_dump(NRT_MemInfo *mi, FILE *out) {
    fprintf(out, "MemInfo %p refcount %zu\n", mi, mi->refct.load());
}

/*
 * Resizable buffer API.
 */

static void
nrt_varsize_dtor(void *ptr, size_t size, void *info) {
    NRT_Debug(nrt_debug_print("nrt_varsize_dtor %p\n", ptr));
    if (info) {
        /* call element dtor */
        typedef void dtor_fn_t(void *ptr);
        dtor_fn_t *dtor = (dtor_fn_t *)info;
        dtor(ptr);
    }
    NRT_Free(ptr);
}

NRT_MemInfo *NRT_MemInfo_new_varsize(size_t size)
{
    NRT_MemInfo *mi;
    void *data = NRT_Allocate(size);
    if (data == NULL)
        return NULL;

    mi = NRT_MemInfo_new(data, size, nrt_varsize_dtor, NULL);
    NRT_Debug(nrt_debug_print("NRT_MemInfo_new_varsize size=%zu "
                              "-> meminfo=%p, data=%p\n", size, mi, data));
    return mi;
}

NRT_MemInfo *NRT_MemInfo_new_varsize_dtor(size_t size, NRT_dtor_function dtor) {
    NRT_MemInfo *mi = NRT_MemInfo_new_varsize(size);
    if (mi) {
        mi->dtor_info = (void*)dtor;
    }
    return mi;
}

extern "C" void *NRT_MemInfo_varsize_alloc(NRT_MemInfo *mi, size_t size)
{
    if (mi->dtor != nrt_varsize_dtor) {
        nrt_fatal_error("ERROR: NRT_MemInfo_varsize_alloc called "
                        "with a non varsize-allocated meminfo");
        return NULL;  /* unreachable */
    }
    mi->data = NRT_Allocate(size);
    if (mi->data == NULL)
        return NULL;
    mi->size = size;
    NRT_Debug(nrt_debug_print("NRT_MemInfo_varsize_alloc %p size=%zu "
                              "-> data=%p\n", mi, size, mi->data));
    return mi->data;
}

extern "C" void *NRT_MemInfo_varsize_realloc(NRT_MemInfo *mi, size_t size)
{
    if (mi->dtor != nrt_varsize_dtor) {
        nrt_fatal_error("ERROR: NRT_MemInfo_varsize_realloc called "
                        "with a non varsize-allocated meminfo");
        return NULL;  /* unreachable */
    }
    mi->data = NRT_Reallocate(mi->data, size);
    if (mi->data == NULL)
        return NULL;
    mi->size = size;
    NRT_Debug(nrt_debug_print("NRT_MemInfo_varsize_realloc %p size=%zu "
                              "-> data=%p\n", mi, size, mi->data));
    return mi->data;
}

extern "C" void NRT_MemInfo_varsize_free(NRT_MemInfo *mi, void *ptr)
{
    NRT_Free(ptr);
    if (ptr == mi->data)
        mi->data = NULL;
}

/*
 * Low-level allocation wrappers.
 */

extern "C" void* NRT_Allocate(size_t size) {
    return NRT_Allocate_External(size, NULL);
}

extern "C" void* NRT_Allocate_External(size_t size, NRT_ExternalAllocator *allocator) {
    void *ptr = NULL;
    if (allocator) {
        ptr = allocator->malloc(size, allocator->opaque_data);
        NRT_Debug(nrt_debug_print("NRT_Allocate_External custom bytes=%zu ptr=%p\n", size, ptr));
    } else {
        ptr = TheMSys.allocator.malloc(size);
        NRT_Debug(nrt_debug_print("NRT_Allocate_External bytes=%zu ptr=%p\n", size, ptr));
    }
    TheMSys.stats_alloc++;
    return ptr;
}

extern "C" void *NRT_Reallocate(void *ptr, size_t size) {
    void *new_ptr = TheMSys.allocator.realloc(ptr, size);
    NRT_Debug(nrt_debug_print("NRT_Reallocate bytes=%zu ptr=%p -> %p\n",
                              size, ptr, new_ptr));
    return new_ptr;
}

extern "C" void NRT_Free(void *ptr) {
    NRT_Debug(nrt_debug_print("NRT_Free %p\n", ptr));
    TheMSys.allocator.free(ptr);
    TheMSys.stats_free++;;
}

/*
 * Sample external allocator implementation for internal testing.
 */

static int sample_external_opaque_data = 0xabacad;

static
void* sample_external_malloc(size_t size, void* opaque_data) {
    if (opaque_data != &sample_external_opaque_data) return NULL;
    return TheMSys.allocator.malloc(size);
}

static
void* sample_external_realloc(void *ptr, size_t new_size, void *opaque_data) {
    if (opaque_data != &sample_external_opaque_data) return NULL;
    return TheMSys.allocator.realloc(ptr, new_size);
}

static
void sample_external_free(void *ptr, void* opaque_data) {
    TheMSys.allocator.free(ptr);
}

static NRT_ExternalAllocator sample_external_allocator = {
    // malloc
    sample_external_malloc,
    // realloc
    sample_external_realloc,
    // free
    sample_external_free,
    // opaque_data
    &sample_external_opaque_data
};

extern "C" NRT_ExternalAllocator* _nrt_get_sample_external_allocator() {
    return &sample_external_allocator;
}

/*
 * Debugging printf function used internally
 */
static
void nrt_debug_print(char *fmt, ...) {
   va_list args;

   va_start(args, fmt);
   vfprintf(stderr, fmt, args);
   va_end(args);
}


static
void nrt_manage_memory_dtor(void *data, size_t size, void *info) {
    NRT_managed_dtor* dtor = (NRT_managed_dtor*)info;
    dtor(data);
}

static
NRT_MemInfo* nrt_manage_memory(void *data, NRT_managed_dtor dtor) {
    return (NRT_MemInfo*)(NRT_MemInfo_new(data, 0, nrt_manage_memory_dtor, (void*)dtor));
}


static const
NRT_api_functions nrt_functions_table = {
    NRT_MemInfo_alloc,
    NRT_MemInfo_alloc_external,
    nrt_manage_memory,
    NRT_MemInfo_acquire,
    NRT_MemInfo_release,
    NRT_MemInfo_data
};


extern "C" const NRT_api_functions* NRT_get_api(void) {
    return &nrt_functions_table;
}
