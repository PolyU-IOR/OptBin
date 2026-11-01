#!/usr/bin/env python3
"""Run with an isolated, installed Python package, outside the source checkout."""
import json
from pathlib import Path
import struct
import sys

import optbin
from optbin import Model, _core


def main():
    path = Path(sys.argv[1])
    expected_version = sys.argv[2]
    assert optbin.__version__ == _core.__version__ == expected_version
    assert Path(optbin.__file__).is_relative_to(Path(sys.prefix)), optbin.__file__
    assert Path(_core.__file__).is_relative_to(Path(sys.prefix)), _core.__file__
    assert not any(p in sys.modules for p in ['numpy', 'scipy', 'h5py'])
    with Model(path) as model:
        assert (model.info['rows'], model.info['cols'], model.info['profile']) == (2, 2, 1)
        assert model.info['objective_offset'] == 7.
        expected = {1: ('Q', [0, 2, 4]), 2: ('I', [0, 1, 0, 1]),
                    3: ('d', [1., -3., -1., 4.]), 4: ('d', [1., -2.]),
                    9: ('Q', [0, 1, 3]), 10: ('I', [0, 0, 1]), 11: ('d', [2., .5, 4.])}
        for section, (code, values) in expected.items():
            assert model.raw(section) == struct.pack('<' + code * len(values), *values), section
        assert model._lib._name == _core.__file__
    assert model.closed
    try:
        Model(path, decoded_limit=1)
    except ValueError:
        pass
    else:
        raise AssertionError('decoded-byte budget not enforced')
    for limit in [-1, 2**64, 2**64+1]:
        try:
            Model(path, decoded_limit=limit)
        except ValueError:
            pass
        else:
            raise AssertionError('out-of-range decoded-byte budget accepted')
    try:
        Model(str(path) + '\0suffix')
    except ValueError:
        pass
    else:
        raise AssertionError('model path silently truncated at null byte')
    print(json.dumps({'version': optbin.__version__, 'bundled_reader': True,
                      'numerical_arrays_equal': True, 'budget_rejection': True,
                      'invalid_budget_and_path_rejection': True}))


if __name__ == '__main__':
    main()
