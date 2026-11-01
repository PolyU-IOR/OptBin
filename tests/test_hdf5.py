"""Identical-codec HDF5 frontend checks; native core itself needs no HDF5."""
import json
import struct
import unittest
import zlib
import test_optbin as base
from optbin import Model


class Hdf5Tests(unittest.TestCase):
    setUp=base.OptBinTests.setUp
    tearDown=base.OptBinTests.tearDown
    convert=base.OptBinTests.convert

    def setUp(self):
        base.OptBinTests.setUp(self)
        try:import h5py,numpy
        except ImportError as e:self.skipTest(str(e))

    def test_exact_sections_and_limit(self):
        from optbin.hdf5 import write_encoded,EncodedModel
        for kind,text in [('mps',base.QP),('cbf',base.PSD)]:
            p=self.convert(text,format=kind,name=kind,args=['--block','2'])
            h5=self.root/(kind+'.h5');write_encoded(p,h5)
            with Model(p) as original,EncodedModel(h5) as decoded:
                self.assertEqual(original.info,decoded.info)
                for id in original.sections:self.assertEqual(original.raw(id),decoded.raw(id))
            with self.assertRaisesRegex(ValueError,'limit'):EncodedModel(h5,decoded_limit=1)
            for limit in [-1,2**64,2**64+1]:
                with self.assertRaisesRegex(ValueError,'decoded_limit'):EncodedModel(h5,decoded_limit=limit)
            with self.assertRaisesRegex(TypeError,'integer'):EncodedModel(h5,decoded_limit=1.5)

    def test_payload_and_metadata_corruption(self):
        import h5py
        from optbin.hdf5 import write_encoded,EncodedModel
        p=self.convert(base.QP);h5=self.root/'bad.h5';write_encoded(p,h5)
        with h5py.File(h5,'r+') as f:
            data=f['3'];data[64]=int(data[64])^1
        with self.assertRaises(ValueError):EncodedModel(h5)
        h5.unlink();write_encoded(p,h5)
        with h5py.File(h5,'r+') as f:f.attrs['manifest']+=' '
        with self.assertRaisesRegex(ValueError,'manifest checksum'):EncodedModel(h5)

    def test_full_validation_after_decode(self):
        import h5py
        from optbin.hdf5 import write_encoded,EncodedModel
        p=self.convert(base.QP);h5=self.root/'bad.h5';write_encoded(p,h5)
        with h5py.File(h5,'r+') as f:
            meta=json.loads(f.attrs['manifest']);meta['info']['rows']=99
            text=json.dumps(meta);f.attrs['manifest']=text;f.attrs['manifest_crc32']=zlib.crc32(text.encode())
        with self.assertRaises(ValueError):EncodedModel(h5)


if __name__=='__main__':unittest.main(verbosity=2)
