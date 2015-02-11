#include "_pymodule.h"
#include <stdio.h>
#include <math.h>
#include "_math_c99.h"
#ifdef _MSC_VER
    #define int64_t signed __int64
    #define uint64_t unsigned __int64
#else
    #include <stdint.h>
#endif
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/ndarrayobject.h>
#include <numpy/arrayscalars.h>

/* For Numpy 1.6 */
#ifndef NPY_ARRAY_BEHAVED
    #define NPY_ARRAY_BEHAVED NPY_BEHAVED
#endif


static const double sqrtpi = 1.772453850905516027298167483341145182798;


/* provide 64-bit division function to 32-bit platforms */
static
int64_t Numba_sdiv(int64_t a, int64_t b) {
    return a / b;
}

static
uint64_t Numba_udiv(uint64_t a, uint64_t b) {
    return a / b;
}

/* provide 64-bit remainder function to 32-bit platforms */
static
int64_t Numba_srem(int64_t a, int64_t b) {
    return a % b;
}

static
uint64_t Numba_urem(uint64_t a, uint64_t b) {
    return a % b;
}

/* provide frexp and ldexp; these wrappers deal with special cases
 * (zero, nan, infinity) directly, to sidestep platform differences.
 */
static
double Numba_frexp(double x, int *exp)
{
    if (!Py_IS_FINITE(x) || !x)
        *exp = 0;
    else
        x = frexp(x, exp);
    return x;
}

static
float Numba_frexpf(float x, int *exp)
{
    if (Py_IS_NAN(x) || Py_IS_INFINITY(x) || !x)
        *exp = 0;
    else
        x = frexpf(x, exp);
    return x;
}

static
double Numba_ldexp(double x, int exp)
{
    if (Py_IS_FINITE(x) && x && exp)
        x = ldexp(x, exp);
    return x;
}

static
float Numba_ldexpf(float x, int exp)
{
    if (Py_IS_FINITE(x) && x && exp)
        x = ldexpf(x, exp);
    return x;
}

/* provide complex power */
static
void Numba_cpow(Py_complex *a, Py_complex *b, Py_complex *c) {
    *c = _Py_c_pow(*a, *b);
}

/* provide erf() and erfc(); code borrowed from CPython */

/*
   Implementations of the error function erf(x) and the complementary error
   function erfc(x).

   Method: following 'Numerical Recipes' by Flannery, Press et. al. (2nd ed.,
   Cambridge University Press), we use a series approximation for erf for
   small x, and a continued fraction approximation for erfc(x) for larger x;
   combined with the relations erf(-x) = -erf(x) and erfc(x) = 1.0 - erf(x),
   this gives us erf(x) and erfc(x) for all x.

   The series expansion used is:

      erf(x) = x*exp(-x*x)/sqrt(pi) * [
                     2/1 + 4/3 x**2 + 8/15 x**4 + 16/105 x**6 + ...]

   The coefficient of x**(2k-2) here is 4**k*factorial(k)/factorial(2*k).
   This series converges well for smallish x, but slowly for larger x.

   The continued fraction expansion used is:

      erfc(x) = x*exp(-x*x)/sqrt(pi) * [1/(0.5 + x**2 -) 0.5/(2.5 + x**2 - )
                              3.0/(4.5 + x**2 - ) 7.5/(6.5 + x**2 - ) ...]

   after the first term, the general term has the form:

      k*(k-0.5)/(2*k+0.5 + x**2 - ...).

   This expansion converges fast for larger x, but convergence becomes
   infinitely slow as x approaches 0.0.  The (somewhat naive) continued
   fraction evaluation algorithm used below also risks overflow for large x;
   but for large x, erfc(x) == 0.0 to within machine precision.  (For
   example, erfc(30.0) is approximately 2.56e-393).

   Parameters: use series expansion for abs(x) < ERF_SERIES_CUTOFF and
   continued fraction expansion for ERF_SERIES_CUTOFF <= abs(x) <
   ERFC_CONTFRAC_CUTOFF.  ERFC_SERIES_TERMS and ERFC_CONTFRAC_TERMS are the
   numbers of terms to use for the relevant expansions.  */

#define ERF_SERIES_CUTOFF 1.5
#define ERF_SERIES_TERMS 25
#define ERFC_CONTFRAC_CUTOFF 30.0
#define ERFC_CONTFRAC_TERMS 50

/*
   Error function, via power series.

   Given a finite float x, return an approximation to erf(x).
   Converges reasonably fast for small x.
*/

static double
m_erf_series(double x)
{
    double x2, acc, fk, result;
    int i, saved_errno;

    x2 = x * x;
    acc = 0.0;
    fk = (double)ERF_SERIES_TERMS + 0.5;
    for (i = 0; i < ERF_SERIES_TERMS; i++) {
        acc = 2.0 + x2 * acc / fk;
        fk -= 1.0;
    }
    /* Make sure the exp call doesn't affect errno;
       see m_erfc_contfrac for more. */
    saved_errno = errno;
    result = acc * x * exp(-x2) / sqrtpi;
    errno = saved_errno;
    return result;
}

/*
   Complementary error function, via continued fraction expansion.

   Given a positive float x, return an approximation to erfc(x).  Converges
   reasonably fast for x large (say, x > 2.0), and should be safe from
   overflow if x and nterms are not too large.  On an IEEE 754 machine, with x
   <= 30.0, we're safe up to nterms = 100.  For x >= 30.0, erfc(x) is smaller
   than the smallest representable nonzero float.  */

static double
m_erfc_contfrac(double x)
{
    double x2, a, da, p, p_last, q, q_last, b, result;
    int i, saved_errno;

    if (x >= ERFC_CONTFRAC_CUTOFF)
        return 0.0;

    x2 = x*x;
    a = 0.0;
    da = 0.5;
    p = 1.0; p_last = 0.0;
    q = da + x2; q_last = 1.0;
    for (i = 0; i < ERFC_CONTFRAC_TERMS; i++) {
        double temp;
        a += da;
        da += 2.0;
        b = da + x2;
        temp = p; p = b*p - a*p_last; p_last = temp;
        temp = q; q = b*q - a*q_last; q_last = temp;
    }
    /* Issue #8986: On some platforms, exp sets errno on underflow to zero;
       save the current errno value so that we can restore it later. */
    saved_errno = errno;
    result = p / q * x * exp(-x2) / sqrtpi;
    errno = saved_errno;
    return result;
}

/* Error function erf(x), for general x */

static double
Numba_erf(double x)
{
    double absx, cf;

    if (Py_IS_NAN(x))
        return x;
    absx = fabs(x);
    if (absx < ERF_SERIES_CUTOFF)
        return m_erf_series(x);
    else {
        cf = m_erfc_contfrac(absx);
        return x > 0.0 ? 1.0 - cf : cf - 1.0;
    }
}

static float
Numba_erff(float x)
{
    return (float) Numba_erf(x);
}

/* Complementary error function erfc(x), for general x. */

static double
Numba_erfc(double x)
{
    double absx, cf;

    if (Py_IS_NAN(x))
        return x;
    absx = fabs(x);
    if (absx < ERF_SERIES_CUTOFF)
        return 1.0 - m_erf_series(x);
    else {
        cf = m_erfc_contfrac(absx);
        return x > 0.0 ? cf : 2.0 - cf;
    }
}

static float
Numba_erfcf(float x)
{
    return (float) Numba_erfc(x);
}


static
int Numba_complex_adaptor(PyObject* obj, Py_complex *out) {
    PyObject* fobj;
    PyArray_Descr *dtype;
    double val[2];

    // Convert from python complex or numpy complex128
    if (PyComplex_Check(obj)) {
        out->real = PyComplex_RealAsDouble(obj);
        out->imag = PyComplex_ImagAsDouble(obj);
    }
    // Convert from numpy complex64
    else if (PyArray_IsScalar(obj, ComplexFloating)) {
        dtype = PyArray_DescrFromScalar(obj);
        if (dtype == NULL) {
            return 0;
        }
        if (PyArray_CastScalarDirect(obj, dtype, &val[0], NPY_CDOUBLE) < 0) {
            Py_DECREF(dtype);
            return 0;
        }
        out->real = val[0];
        out->imag = val[1];
        Py_DECREF(dtype);
    } else {
        fobj = PyNumber_Float(obj);
        if (!fobj) return 0;
        out->real = PyFloat_AsDouble(fobj);
        out->imag = 0.;
        Py_DECREF(fobj);
    }
    return 1;
}

/* Minimum PyBufferObject structure to hack inside it */
typedef struct {
    PyObject_HEAD
    PyObject *b_base;
    void *b_ptr;
    Py_ssize_t b_size;
    Py_ssize_t b_offset;
}  PyBufferObject_Hack;

/*
Get data address of record data buffer
*/
static
void* Numba_extract_record_data(PyObject *recordobj, Py_buffer *pbuf) {
    PyObject *attrdata;
    void *ptr;

    attrdata = PyObject_GetAttrString(recordobj, "data");
    if (!attrdata) return NULL;

    if (-1 == PyObject_GetBuffer(attrdata, pbuf, 0)){
        #if PY_MAJOR_VERSION >= 3
            Py_DECREF(attrdata);
            return NULL;
        #else
            /* HACK!!! */
            /* In Python 2.6, it will report no buffer interface for record
               even though it should */
            PyBufferObject_Hack *hack;

            /* Clear the error */
            PyErr_Clear();

            hack = (PyBufferObject_Hack*) attrdata;

            if (hack->b_base == NULL) {
                ptr = hack->b_ptr;
            } else {
                PyBufferProcs *bp;
                readbufferproc proc = NULL;

                bp = hack->b_base->ob_type->tp_as_buffer;
                /* FIXME Ignoring any flag.  Just give me the pointer */
                proc = (readbufferproc)bp->bf_getreadbuffer;
                if ((*proc)(hack->b_base, 0, &ptr) <= 0) {
                    Py_DECREF(attrdata);
                    return NULL;
                }
                ptr = (char*)ptr + hack->b_offset;
            }
        #endif
    } else {
        ptr = pbuf->buf;
    }
    Py_DECREF(attrdata);
    return ptr;
}

/*
 * Return a record instance with dtype as the record type, and backed
 * by a copy of the memory area pointed to by (pdata, size).
 */
static
PyObject* Numba_recreate_record(void *pdata, int size, PyObject *dtype) {
    PyObject *numpy = NULL;
    PyObject *numpy_record = NULL;
    PyObject *aryobj = NULL;
    PyObject *dtypearg = NULL;
    PyObject *record = NULL;
    PyArray_Descr *descr = NULL;

    numpy = PyImport_ImportModuleNoBlock("numpy");
    if (!numpy) goto CLEANUP;

    numpy_record = PyObject_GetAttrString(numpy, "record");
    if (!numpy_record) goto CLEANUP;

    dtypearg = PyTuple_Pack(2, numpy_record, dtype);
    if (!dtypearg || !PyArray_DescrConverter(dtypearg, &descr))
        goto CLEANUP;

    /* This steals a reference to descr, so we don't have to DECREF it */
    aryobj = PyArray_FromString(pdata, size, descr, 1, NULL);
    if (!aryobj) goto CLEANUP;

    record = PySequence_GetItem(aryobj, 0);

CLEANUP:
    Py_XDECREF(numpy);
    Py_XDECREF(numpy_record);
    Py_XDECREF(aryobj);
    Py_XDECREF(dtypearg);

    return record;
}

/*
 * Fill in the *arystruct* with information from the Numpy array *obj*.
 * *arystruct*'s layout is defined in numba.targets.arrayobj (look
 * for the ArrayTemplate class).
 */

typedef struct {
    PyObject *parent;
    npy_intp nitems;
    npy_intp itemsize;
    void *data;
    npy_intp shape_and_strides[];
} arystruct_t;

static
int Numba_adapt_ndarray(PyObject *obj, arystruct_t* arystruct) {
    PyArrayObject *ndary;
    int i, ndim;
    npy_intp *p;

    if (!PyArray_Check(obj)) {
        return -1;
    }

    ndary = (PyArrayObject*)obj;
    ndim = PyArray_NDIM(ndary);

    arystruct->data = PyArray_DATA(ndary);
    arystruct->nitems = PyArray_SIZE(ndary);
    arystruct->itemsize = PyArray_ITEMSIZE(ndary);
    arystruct->parent = obj;
    p = arystruct->shape_and_strides;
    for (i = 0; i < ndim; i++, p++) {
        *p = PyArray_DIM(ndary, i);
    }
    for (i = 0; i < ndim; i++, p++) {
        *p = PyArray_STRIDE(ndary, i);
    }

    return 0;
}

static
PyObject* Numba_ndarray_new(int nd,
                            npy_intp *dims,   /* shape */
                            npy_intp *strides,
                            void* data,
                            int type_num,
                            int itemsize)
{
    PyObject *ndary;
    int flags = NPY_ARRAY_BEHAVED;
    ndary = PyArray_New((PyTypeObject*)&PyArray_Type, nd, dims, type_num,
                       strides, data, 0, flags, NULL);
    return ndary;
}

/* We use separate functions for datetime64 and timedelta64, to ensure
 * proper type checking.
 */
static npy_int64
Numba_extract_np_datetime(PyObject *td)
{
    if (!PyArray_IsScalar(td, Datetime)) {
        PyErr_SetString(PyExc_TypeError,
                        "expected a numpy.datetime64 object");
        return -1;
    }
    return PyArrayScalar_VAL(td, Timedelta);
}

static npy_int64
Numba_extract_np_timedelta(PyObject *td)
{
    if (!PyArray_IsScalar(td, Timedelta)) {
        PyErr_SetString(PyExc_TypeError,
                        "expected a numpy.timedelta64 object");
        return -1;
    }
    return PyArrayScalar_VAL(td, Timedelta);
}

static PyObject *
Numba_create_np_datetime(npy_int64 value, int unit_code)
{
    PyDatetimeScalarObject *obj = (PyDatetimeScalarObject *)
        PyArrayScalar_New(Datetime);
    if (obj != NULL) {
        obj->obval = value;
        obj->obmeta.base = unit_code;
        obj->obmeta.num = 1;
    }
    return (PyObject *) obj;
}

static PyObject *
Numba_create_np_timedelta(npy_int64 value, int unit_code)
{
    PyTimedeltaScalarObject *obj = (PyTimedeltaScalarObject *)
        PyArrayScalar_New(Timedelta);
    if (obj != NULL) {
        obj->obval = value;
        obj->obmeta.base = unit_code;
        obj->obmeta.num = 1;
    }
    return (PyObject *) obj;
}

static
double Numba_round_even(double y) {
    double z = round(y);
    if (fabs(y-z) == 0.5) {
        /* halfway between two integers; use round-half-even */
        z = 2.0*round(y / 2.0);
    }
    return z;
}

static
float Numba_roundf_even(float y) {
    float z = roundf(y);
    if (fabsf(y-z) == 0.5) {
        /* halfway between two integers; use round-half-even */
        z = 2.0 * roundf(y / 2.0);
    }
    return z;
}

static
uint64_t Numba_fptoui(double x) {
    /* First cast to signed int of the full width to make sure sign extension
       happens (this can make a difference on some platforms...). */
    return (uint64_t) (int64_t) x;
}

static
uint64_t Numba_fptouif(float x) {
    return (uint64_t) (int64_t) x;
}

static
void Numba_release_record_buffer(Py_buffer *buf)
{
    PyBuffer_Release(buf);
}


static
void Numba_gil_ensure(PyGILState_STATE *state) {
    *state = PyGILState_Ensure();
}

static
void Numba_gil_release(PyGILState_STATE *state) {
    PyGILState_Release(*state);
}

/*
Define bridge for all math functions
*/
#define MATH_UNARY(F, R, A) static R Numba_##F(A a) { return F(a); }
#define MATH_BINARY(F, R, A, B) static R Numba_##F(A a, B b) \
                                       { return F(a, b); }
    #include "mathnames.inc"
#undef MATH_UNARY
#undef MATH_BINARY

/*
Expose all functions
*/

static PyObject *
build_c_helpers_dict(void)
{
    PyObject *dct = PyDict_New();
    if (dct == NULL)
        goto error;

#define declmethod(func) do {                          \
    PyObject *val = PyLong_FromVoidPtr(&Numba_##func); \
    if (val == NULL) goto error;                       \
    if (PyDict_SetItemString(dct, #func, val)) {       \
        Py_DECREF(val);                                \
        goto error;                                    \
    }                                                  \
    Py_DECREF(val);                                    \
} while (0)

    declmethod(sdiv);
    declmethod(srem);
    declmethod(udiv);
    declmethod(urem);
    declmethod(frexp);
    declmethod(frexpf);
    declmethod(ldexp);
    declmethod(ldexpf);
    declmethod(cpow);
    declmethod(erf);
    declmethod(erff);
    declmethod(erfc);
    declmethod(erfcf);
    declmethod(complex_adaptor);
    declmethod(extract_record_data);
    declmethod(release_record_buffer);
    declmethod(adapt_ndarray);
    declmethod(ndarray_new);
    declmethod(extract_np_datetime);
    declmethod(create_np_datetime);
    declmethod(extract_np_timedelta);
    declmethod(create_np_timedelta);
    declmethod(recreate_record);
    declmethod(round_even);
    declmethod(roundf_even);
    declmethod(fptoui);
    declmethod(fptouif);
    declmethod(gil_ensure);
    declmethod(gil_release);
#define MATH_UNARY(F, R, A) declmethod(F);
#define MATH_BINARY(F, R, A, B) declmethod(F);
    #include "mathnames.inc"
#undef MATH_UNARY
#undef MATH_BINARY

#undef declmethod
    return dct;
error:
    Py_XDECREF(dct);
    return NULL;
}

static PyMethodDef ext_methods[] = {
    { NULL },
};


MOD_INIT(_helperlib) {
    PyObject *m;
    MOD_DEF(m, "_helperlib", "No docs", ext_methods)
    if (m == NULL)
        return MOD_ERROR_VAL;

    import_array();

    PyModule_AddObject(m, "c_helpers", build_c_helpers_dict());
    PyModule_AddIntConstant(m, "long_min", LONG_MIN);
    PyModule_AddIntConstant(m, "long_max", LONG_MAX);
    PyModule_AddIntConstant(m, "py_buffer_size", sizeof(Py_buffer));
    PyModule_AddIntConstant(m, "py_gil_state_size", sizeof(PyGILState_STATE));

    return MOD_SUCCESS_VAL(m);
}
