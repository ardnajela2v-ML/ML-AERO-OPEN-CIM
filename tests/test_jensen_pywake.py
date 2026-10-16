"""Optional external-reference checks, collected by the normal pytest suite."""
import importlib.util
import unittest

from validation.control_case_jensen_pywake import build_control, validate_case


@unittest.skipUnless(importlib.util.find_spec('py_wake'),
                     'Install validation/requirements-jensen.txt for PyWake validation')
class JensenPyWakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.turbine, cls.model = build_control()

    def test_aligned(self):
        validate_case('aligned', self.turbine, self.model)

    def test_partial_overlap(self):
        validate_case('partial_overlap', self.turbine, self.model)

    def test_outside_wake(self):
        validate_case('outside_wake', self.turbine, self.model)
