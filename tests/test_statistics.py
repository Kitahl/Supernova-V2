from __future__ import annotations

import itertools
import math
import sys
import unittest
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supernova_goal1.statistics import holm_step_down, mcnemar_exact_two_sided


def reference_exact_binomial_two_sided(successes: int, trials: int) -> float:
    if trials == 0:
        return 1.0
    observed_weight = math.comb(trials, successes)
    numerator = sum(
        math.comb(trials, index)
        for index in range(trials + 1)
        if math.comb(trials, index) <= observed_weight
    )
    return numerator / (2**trials)


def reference_doubled_smaller_tail(candidate_only: int, control_only: int) -> float:
    trials = candidate_only + control_only
    if trials == 0:
        return 1.0
    smaller = min(candidate_only, control_only)
    tail = sum(math.comb(trials, index) for index in range(smaller + 1))
    exact = Fraction(2 * tail, 1 << trials)
    return float(min(Fraction(1, 1), exact))


def reference_holm(p_values: tuple[float, ...], alpha: float) -> tuple[bool, ...]:
    order = sorted(range(len(p_values)), key=p_values.__getitem__)
    decisions = [False] * len(p_values)
    still_rejecting = True
    for position, index in enumerate(order):
        cutoff = alpha / (len(p_values) - position)
        if still_rejecting and p_values[index] <= cutoff:
            decisions[index] = True
        else:
            still_rejecting = False
    return tuple(decisions)


class McNemarExactTests(unittest.TestCase):
    def test_no_discordant_pairs_is_one(self) -> None:
        self.assertEqual(1.0, mcnemar_exact_two_sided(0, 0))

    def test_known_boundaries(self) -> None:
        self.assertEqual(1.0, mcnemar_exact_two_sided(0, 1))
        self.assertEqual(0.0625, mcnemar_exact_two_sided(0, 5))
        self.assertEqual(0.03125, mcnemar_exact_two_sided(0, 6))
        self.assertEqual(1.0, mcnemar_exact_two_sided(2, 2))

    def test_large_discordant_counts_match_exact_rational_oracle(self) -> None:
        cases = (
            (0, 1024),
            (1, 1023),
            (17, 1007),
            (512, 512),
            (0, 1075),
            (0, 1076),
            (0, 2048),
            (256, 1792),
            (1024, 1024),
        )
        for candidate_only, control_only in cases:
            with self.subTest(candidate_only=candidate_only, control_only=control_only):
                expected = reference_doubled_smaller_tail(candidate_only, control_only)
                actual = mcnemar_exact_two_sided(candidate_only, control_only)
                self.assertEqual(expected, actual)
                self.assertEqual(actual, mcnemar_exact_two_sided(control_only, candidate_only))
                self.assertGreaterEqual(actual, 0.0)
                self.assertLessEqual(actual, 1.0)

    def test_1024_boundary_is_finite_and_exactly_representable(self) -> None:
        expected = math.ldexp(1.0, -1023)
        actual = mcnemar_exact_two_sided(0, 1024)
        self.assertEqual(expected, actual)
        self.assertTrue(math.isfinite(actual))
        self.assertGreater(actual, 0.0)

    def test_large_tail_is_monotone_toward_balance(self) -> None:
        smaller_counts = (0, 1, 2, 8, 16, 64, 128, 256, 384, 512)
        p_values = [
            mcnemar_exact_two_sided(smaller, 1024 - smaller)
            for smaller in smaller_counts
        ]
        self.assertEqual(p_values, sorted(p_values))
        self.assertEqual(1.0, p_values[-1])

    def test_property_matches_independent_exact_binomial_oracle(self) -> None:
        for candidate_only in range(13):
            for control_only in range(13):
                with self.subTest(candidate_only=candidate_only, control_only=control_only):
                    trials = candidate_only + control_only
                    expected = reference_exact_binomial_two_sided(candidate_only, trials)
                    self.assertAlmostEqual(
                        expected,
                        mcnemar_exact_two_sided(candidate_only, control_only),
                        places=15,
                    )

    def test_property_is_symmetric_and_bounded(self) -> None:
        for candidate_only in range(20):
            for control_only in range(20):
                actual = mcnemar_exact_two_sided(candidate_only, control_only)
                self.assertEqual(
                    actual,
                    mcnemar_exact_two_sided(control_only, candidate_only),
                )
                self.assertGreaterEqual(actual, 0.0)
                self.assertLessEqual(actual, 1.0)

    def test_rejects_invalid_counts(self) -> None:
        for args in ((-1, 0), (0, -1), (1.5, 0), (True, 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mcnemar_exact_two_sided(*args)

    def test_rejects_hostile_int_subclasses(self) -> None:
        class EvilInt(int):
            def __lt__(self, other: object) -> bool:
                return False

            def __add__(self, other: object) -> int:
                return 0

        with self.assertRaises(ValueError):
            mcnemar_exact_two_sided(EvilInt(-9), 0)
        with self.assertRaises(ValueError):
            mcnemar_exact_two_sided(0, EvilInt(4))


class HolmTests(unittest.TestCase):
    def test_empty_family_is_empty(self) -> None:
        self.assertEqual((), holm_step_down([], 0.05))

    def test_step_down_stops_after_first_failure(self) -> None:
        result = holm_step_down([0.001, 0.02, 0.04, 0.2], 0.05)
        self.assertEqual([True, False, False, False], [item.rejects_null for item in result])
        self.assertEqual(0.0125, result[0].threshold)
        self.assertAlmostEqual(0.05 / 3, result[1].threshold)
        self.assertEqual(0.025, result[2].threshold)
        self.assertEqual(0.05, result[3].threshold)

    def test_boundary_uses_less_than_or_equal(self) -> None:
        result = holm_step_down([0.025, 0.04], 0.05)
        self.assertEqual([True, True], [item.rejects_null for item in result])

    def test_ties_are_stable_and_results_return_to_input_order(self) -> None:
        result = holm_step_down([0.02, 0.001, 0.02], 0.05)
        self.assertEqual([0.02, 0.001, 0.02], [item.p_value for item in result])
        self.assertAlmostEqual(0.025, result[0].threshold)
        self.assertAlmostEqual(0.05 / 3, result[1].threshold)
        self.assertAlmostEqual(0.05, result[2].threshold)

    def test_property_matches_independent_holm_oracle(self) -> None:
        grid = (0.0, 0.01, 0.025, 0.05, 0.2, 1.0)
        for size in range(1, 5):
            for p_values in itertools.product(grid, repeat=size):
                with self.subTest(p_values=p_values):
                    expected = reference_holm(p_values, 0.05)
                    actual = tuple(
                        item.rejects_null for item in holm_step_down(p_values, 0.05)
                    )
                    self.assertEqual(expected, actual)

    def test_property_rejections_are_monotone_in_alpha(self) -> None:
        families = (
            (0.001, 0.01, 0.04, 0.2),
            (0.0125, 0.02, 0.03, 0.049),
            (0.5, 0.5, 0.5),
        )
        for p_values in families:
            strict = holm_step_down(p_values, 0.01)
            loose = holm_step_down(p_values, 0.05)
            for strict_item, loose_item in zip(strict, loose, strict=True):
                self.assertFalse(strict_item.rejects_null and not loose_item.rejects_null)

    def test_rejects_invalid_alpha_and_p_values(self) -> None:
        for alpha in (0, 1, -0.1, 1.1, True, math.inf, -math.inf, math.nan):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                holm_step_down([0.01], alpha)
        for p_values in (
            (-0.1,),
            (1.1,),
            (True,),
            ("0.1",),
            (math.inf,),
            (-math.inf,),
            (math.nan,),
        ):
            with self.subTest(p_values=p_values), self.assertRaises(ValueError):
                holm_step_down(p_values, 0.05)

    def test_rejects_hostile_numeric_subclasses(self) -> None:
        class EvilFloat(float):
            def __ge__(self, other: object) -> bool:
                return True

            def __le__(self, other: object) -> bool:
                return True

        class EvilInt(int):
            pass

        with self.assertRaises(ValueError):
            holm_step_down([EvilFloat(2.0)], 0.05)
        with self.assertRaises(ValueError):
            holm_step_down([0.01], EvilFloat(0.05))
        with self.assertRaises(ValueError):
            holm_step_down([EvilInt(0)], 0.05)
        with self.assertRaises(ValueError):
            holm_step_down([0.01], EvilInt(0))

    def test_outputs_snapshot_plain_floats(self) -> None:
        result = holm_step_down([0, 1, 0.01], 0.05)
        self.assertTrue(all(type(item.p_value) is float for item in result))
        self.assertTrue(all(type(item.threshold) is float for item in result))


if __name__ == "__main__":
    unittest.main()
