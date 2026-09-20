import unittest
from solution import clamp


class ClampTests(unittest.TestCase):
    def test_in_range(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_lower(self):
        self.assertEqual(clamp(-2, 0, 10), 0)

    def test_upper(self):
        self.assertEqual(clamp(12, 0, 10), 10)

    def test_float(self):
        self.assertEqual(clamp(1.5, 0.0, 2.0), 1.5)

    def test_invalid_bounds(self):
        with self.assertRaises(ValueError):
            clamp(1, 10, 0)


if __name__ == '__main__':
    unittest.main()
