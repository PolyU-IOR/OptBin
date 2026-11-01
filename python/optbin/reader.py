import ctypes as C
from importlib.util import find_spec
import os
import operator
from pathlib import Path


class Info(C.Structure):
    _fields_ = [(n, C.c_uint64) for n in
                ("sections", "blocks", "file_bytes", "cols", "rows", "block_elements")]
    _fields_ += [("profile", C.c_uint32), ("sense", C.c_int32), ("objective_offset", C.c_double)]


class Section(C.Structure):
    _fields_ = [("id", C.c_uint32), ("type", C.c_uint32)]
    _fields_ += [(n, C.c_uint64) for n in ("count", "offset", "stored_bytes", "blocks", "flags")]
    _fields_ += [("data", C.c_void_p)]


class Native(C.Structure):
    _fields_ = [("info", Info), ("section", C.POINTER(Section))]


def library():
    path = os.environ.get("OPTBIN_LIBRARY")
    if not path:
        bundled = find_spec("optbin._core")
        if bundled and bundled.origin:
            path = bundled.origin
        else:
            path = Path(__file__).resolve().parents[2] / "build" / "liboptbin.so"
            if not path.is_file():
                raise RuntimeError("OptBin native reader is missing. Install the package with pip, "
                                   "run make in a source checkout, or set OPTBIN_LIBRARY to liboptbin.so.")
    try:
        lib = C.CDLL(str(path))
    except OSError as error:
        raise RuntimeError(f"Cannot load OptBin reader {path}: {error}") from error
    lib.optb_load.argtypes = [C.c_char_p, C.POINTER(Native), C.c_uint64, C.c_char_p, C.c_size_t]
    lib.optb_load.restype = C.c_int
    lib.optb_free.argtypes = [C.POINTER(Native)]
    lib.optb_free.restype = None
    return lib


def _decoded_byte_limit(value):
    try:
        value = operator.index(value)
    except TypeError:
        raise TypeError('decoded_limit must be an integer') from None
    if not 0 <= value < 1 << 64:
        raise ValueError('decoded_limit must be between 0 and 2^64-1')
    return value


class Model:
    """Owns a decoded native model. Use as a context manager for prompt freeing.

    ``raw`` and ``numpy`` return copies by default. An explicit copy=False NumPy
    view is valid only while this Model remains open. It must not outlive close.
    ``decoded_limit`` is an integer in [0, 2**64-1]; zero means unlimited.
    """
    def __init__(self, path, decoded_limit=0):
        decoded_limit = _decoded_byte_limit(decoded_limit)
        path = os.fsencode(path)
        if b'\0' in path:
            raise ValueError('embedded null byte in model path')
        self._lib = library()
        self._native = Native()
        error = C.create_string_buffer(1024)
        if self._lib.optb_load(path, C.byref(self._native), decoded_limit, error, len(error)):
            raise ValueError(error.value.decode())
        self.info = {n: getattr(self._native.info, n) for n, _ in Info._fields_}
        self._sections = {s.id: s for s in self._native.section[:self.info['sections']]}
        self.closed = False

    def close(self):
        if not getattr(self, 'closed', True):
            self._lib.optb_free(C.byref(self._native))
            self._sections.clear()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        self.close()

    @property
    def sections(self):
        return {i: {'type': s.type, 'count': s.count, 'stored_bytes': s.stored_bytes}
                for i, s in self._sections.items()}

    def raw(self, id):
        if self.closed:
            raise ValueError('model is closed')
        s = self._sections.get(id)
        if s is None:
            return b''
        return C.string_at(s.data, s.count * {1: 4, 2: 8, 3: 8, 4: 1}[s.type]) if s.count else b''

    def numpy(self, id, copy=True):
        import numpy as np
        if self.closed:
            raise ValueError('model is closed')
        s = self._sections.get(id)
        if s is None:
            return np.array([])
        dtype = {1: '<u4', 2: '<u8', 3: '<f8', 4: 'u1'}[s.type]
        if not s.count:
            return np.array([], dtype=dtype)
        buffer = (C.c_ubyte * (s.count * np.dtype(dtype).itemsize)).from_address(s.data)
        result = np.frombuffer(buffer, dtype=dtype)
        return result.copy() if copy else result
