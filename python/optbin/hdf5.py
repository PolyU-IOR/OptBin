"""Experimental HDF5 frontend containing identical OptBin-encoded sections.

HDF5 is optional. This reuses the C block decoder and full model validation,
allowing experiments to separate the encodings from the outer container.
"""
import ctypes as C
import json
from pathlib import Path
import zlib
from .reader import Model,Info,Section,Native,library,_decoded_byte_limit


def write_encoded(source,target):
    import h5py
    import numpy as np
    target=Path(target)
    with open(source,'rb') as f:
        if f.read(8)!=b'OPTBIN2\n':raise ValueError('HDF5 section export requires a v2 OptBin source')
        with Model(source) as model:
            entries=[]
            for id,s in model._sections.items():
                entries.append({'id':id,**{n:getattr(s,n) for n in ('type','count','offset','stored_bytes','blocks','flags')}})
            manifest=json.dumps({'info':model.info,'sections':entries},sort_keys=True,separators=(',',':'))
            with h5py.File(target,'x') as out:
                out.attrs['format']='optbin-encoded-v2'
                out.attrs['manifest']=manifest
                out.attrs['manifest_crc32']=zlib.crc32(manifest.encode())
                for s in entries:
                    f.seek(s['offset']);data=f.read(s['stored_bytes'])
                    if len(data)!=s['stored_bytes']:raise ValueError('truncated source section')
                    out.create_dataset(str(s['id']),data=np.frombuffer(data,dtype='u1'))


class EncodedModel:
    def __init__(self,path,decoded_limit=0):
        decoded_limit=_decoded_byte_limit(decoded_limit)
        import h5py
        import numpy as np
        self.closed=False;self._data={};self._metadata={}
        lib=library()
        lib.optb_decode_section.argtypes=[C.POINTER(Section),C.c_void_p,C.c_size_t,C.c_void_p,C.c_size_t,
                                         C.c_uint64,C.c_void_p,C.c_size_t,C.c_char_p,C.c_size_t]
        lib.optb_decode_section.restype=C.c_int
        lib.optb_validate.argtypes=[C.POINTER(Native),C.c_char_p,C.c_size_t];lib.optb_validate.restype=C.c_int
        scratch=np.empty(16*1024*1024,dtype='u1');error=C.create_string_buffer(1024)
        with h5py.File(path,'r') as f:
            if f.attrs.get('format')!='optbin-encoded-v2':raise ValueError('not an OptBin encoded HDF5 model')
            manifest=f.attrs['manifest']
            if zlib.crc32(manifest.encode())!=f.attrs['manifest_crc32']:raise ValueError('manifest checksum mismatch')
            metadata=json.loads(manifest);self.info=metadata['info'];entries=metadata['sections']
            if len(entries)>4096 or len(entries)!=self.info['sections']:raise ValueError('invalid section count')
            total=0
            previous=0
            for s in entries:
                if not isinstance(s['id'],int) or not previous<s['id']<2**32 or s['flags'] not in (0,1):
                    raise ValueError('invalid section ID/order/flags')
                previous=s['id']
                width={1:4,2:8,3:8,4:1}.get(s['type'],0)
                if not width or not 0<=s['count']<2**63:raise ValueError('invalid section type/count')
                total+=width*s['count']
                if decoded_limit and total>decoded_limit:raise ValueError('decoded arrays exceed limit')
            directory=(Section*len(entries))()
            for k,s in enumerate(entries):
                dtype={1:'<u4',2:'<u8',3:'<f8',4:'u1'}[s['type']]
                array=np.empty(s['count'],dtype=dtype);encoded=f[str(s['id'])][...]
                if encoded.dtype!=np.dtype('u1') or encoded.ndim!=1:raise ValueError('encoded section is not bytes')
                section=Section(s['id'],s['type'],s['count'],0,s['stored_bytes'],s['blocks'],s['flags'],array.ctypes.data)
                if lib.optb_decode_section(C.byref(section),encoded.ctypes.data,encoded.nbytes,array.ctypes.data,array.nbytes,
                        self.info['block_elements'],scratch.ctypes.data,scratch.nbytes,error,len(error)):
                    raise ValueError(error.value.decode())
                self._data[s['id']]=array;self._metadata[s['id']]=s;directory[k]=section
            native=Native(Info(**self.info),directory)
            if lib.optb_validate(C.byref(native),error,len(error)):raise ValueError(error.value.decode())
        self.storage_bytes=Path(path).stat().st_size

    @property
    def sections(self):return {id:{'type':s['type'],'count':s['count'],'stored_bytes':s['stored_bytes']} for id,s in self._metadata.items()}

    def numpy(self,id,copy=True):
        import numpy as np
        if self.closed:raise ValueError('model is closed')
        result=self._data.get(id)
        return np.array([]) if result is None else result.copy() if copy else result

    def raw(self,id):return self.numpy(id,copy=False).tobytes()
    def close(self):self._data.clear();self._metadata.clear();self.closed=True
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
