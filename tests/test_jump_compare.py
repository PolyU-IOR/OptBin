"""The independent-reader comparison must detect numerical/semantic changes."""
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from validate_jump import bits, compare, interval, scalar


class JumpComparisonTests(unittest.TestCase):
    def model(self):
        return {'variables': 2, 'sense': 1, 'objective': scalar([[0, 1.]], [[0, 1, 2.]], 3.),
                'constraints': [interval(scalar([[0, 1.], [1, -1.]], constant=2.), 3., 5.)]}

    def test_representation_normalization(self):
        a = self.model(); b = copy.deepcopy(a)
        b['constraints'] = [interval(scalar([[1, -1.], [0, .5], [0, .5]]), 1., math.inf),
                            interval(scalar([[0, 1.], [1, -1.]]), -math.inf, 3.)]
        b['objective'] = scalar([[0, bits(1.)]], [[1, 0, bits(2.)]], bits(3.))
        result = compare(a, b)
        self.assertTrue(result['pass'])
        self.assertEqual(result['native_canonical_sha256'], result['reference_canonical_sha256'])

    def test_one_ulp_and_quadratic_factor_are_detected(self):
        a = self.model()
        for objective in [scalar([[0, math.nextafter(1., 2.)]], [[0, 1, 2.]], 3.),
                          scalar([[0, 1.]], [[0, 1, 1.]], 3.)]:
            b = copy.deepcopy(a); b['objective'] = objective
            self.assertFalse(compare(a, b)['pass'])

    def test_missing_and_duplicate_constraints_are_detected(self):
        a = self.model()
        for constraints in [[], a['constraints'] * 2]:
            b = copy.deepcopy(a); b['constraints'] = constraints
            self.assertFalse(compare(a, b)['pass'])

    def test_cone_parameters_and_constants_are_detected(self):
        a = self.model()
        a['constraints'].append({'set': {'kind': 'PowerCone', 'dimension': 3, 'exponent': .4},
            'function': {'kind': 'vector', 'terms': [[0, 0, 1.]], 'constants': [1., 2., 3.]}})
        for key, value in [('exponent', .6), ('constant', -3.)]:
            b = copy.deepcopy(a)
            if key == 'exponent':b['constraints'][-1]['set'][key] = value
            else:b['constraints'][-1]['function']['constants'][-1] = value
            self.assertFalse(compare(a, b)['pass'])


if __name__ == '__main__':
    unittest.main()
