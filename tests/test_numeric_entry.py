"""Quantity-entry arithmetic is exact, bounded and distinct from record parsing."""
from decimal import Decimal, Inexact, Rounded, localcontext
import unittest

from flowledger.core import parse_quantity, quantity_text
from flowledger.numeric_entry import evaluate_quantity, MAX_EXPRESSION_LENGTH, MAX_NESTING


class NumericEntryTests(unittest.TestCase):
    def test_arithmetic_precedence_grouping_and_signed_quantities(self):
        examples = {
            "1.23 + 4.56": "5.79",
            "2 + 3 * 4": "14",
            "(2 + 3) * 4": "20",
            "20 / 2 / 5": "2",
            "20 / (2 / 5)": "50",
            "-.5 * (2 - 4)": "1",
            "1 + -2": "-1",
            "-(1 + 2)": "-3",
            "--.25": "0.25",
            "1,234.50 + .5": "1235",
            "1 - 1": "0",
            "-0": "0",
            "  +12.3400  ": "12.34",
            "=1.23 + 4.56": "5.79",
            "  = (2 + 3) * 4": "20",
            "=-.5": "-0.5",
            "=0": "0",
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(quantity_text(evaluate_quantity(text)), expected)

    def test_terminating_math_and_rational_cancellation_ignore_decimal_context(self):
        giant = "12345678901234567890123456789012345678901234567890"
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(quantity_text(evaluate_quantity(giant + " + .000000000000000000001")),
                             giant + ".000000000000000000001")
            self.assertEqual(quantity_text(evaluate_quantity(giant + " * " + giant)), str(int(giant) ** 2))
            self.assertEqual(quantity_text(evaluate_quantity("1 / 128")), "0.0078125")
            self.assertEqual(quantity_text(evaluate_quantity("1 / 3 * 3")), "1")
            self.assertEqual(quantity_text(evaluate_quantity("1 / 3 + 2 / 3")), "1")
            self.assertEqual(quantity_text(evaluate_quantity("0 / .1")), "0")

    def test_repeating_results_round_final_value_once_to_documented_precision(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(quantity_text(evaluate_quantity("1 / 3")), "0." + "3" * 50)
            self.assertEqual(evaluate_quantity("=1 / 3"), evaluate_quantity("1 / 3"))
            self.assertEqual(quantity_text(evaluate_quantity("2 / 3")), "0." + "6" * 49 + "7")
            self.assertEqual(quantity_text(evaluate_quantity("-1 / 3")), "-0." + "3" * 50)
            precise_one = "1." + "0" * 60
            # 61 digits in the first literal plus the divisor digit gives 62.
            self.assertEqual(quantity_text(evaluate_quantity(precise_one + " / 3")), "0." + "3" * 62)

    def test_decimal_literals_do_not_pass_through_binary_floats(self):
        self.assertEqual(evaluate_quantity("0.1 + 0.2"), Decimal("0.3"))
        self.assertEqual(evaluate_quantity("0.1 * 0.1"), Decimal("0.01"))
        self.assertEqual(evaluate_quantity("9007199254740993 - 9007199254740992"), Decimal(1))

    def test_invalid_math_and_code_are_rejected_without_evaluation(self):
        invalid = ("", " ", "=", "= ", "==1+2", "1=2", "=(1=2)", "1e3", "NaN", "Infinity", "$1", "1,2 + 3",
                   "1,,000", "1.", ".", "1 +", "()", "(1 + 2", "1 + 2)", "1 2",
                   "2(3)", "(2)(3)", "1 // 2", "2 ** 3", "2 % 1", "x + 1",
                   "abs(-1)", "__import__('os').system('echo bad')", "[1][0]", True, 1.0, None)
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                evaluate_quantity(text)
        for text in ("1 / 0", "1 / (2 - 2)", "0 / 0"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "divide by zero"):
                evaluate_quantity(text)

    def test_entry_calculations_do_not_change_the_record_parser(self):
        for text in ("1 + 2", "3 / 4", "(5)", "=1+2", "=5"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_quantity(text)
        self.assertEqual(evaluate_quantity("3 / 4"), Decimal("0.75"))

    def test_input_and_nesting_limits_reject_expensive_expressions(self):
        large_literal = "1" * (MAX_EXPRESSION_LENGTH + 1)
        small_literal = "-." + "0" * MAX_EXPRESSION_LENGTH + "1"
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(evaluate_quantity(large_literal), Decimal(large_literal))
            self.assertEqual(evaluate_quantity(small_literal), Decimal(small_literal))
        with self.assertRaisesRegex(ValueError, "too long"):
            evaluate_quantity(large_literal + " + 1")
        with self.assertRaisesRegex(ValueError, "too many nested"):
            evaluate_quantity("(" * (MAX_NESTING + 1) + "1" + ")" * (MAX_NESTING + 1))
        with self.assertRaisesRegex(ValueError, "too many terms"):
            evaluate_quantity("+".join(["1"] * 130))
        self.assertEqual(evaluate_quantity("(" * MAX_NESTING + ".5" + ")" * MAX_NESTING), Decimal(".5"))


if __name__ == "__main__":
    unittest.main()
