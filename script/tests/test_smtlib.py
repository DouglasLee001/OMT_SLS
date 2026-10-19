import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SMTLibTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((Path(__file__).resolve().parents[1]/'smtlib.py').is_file(), 'SMT-LIB objective/value parser is not implemented')
        import smtlib
        self.smtlib = smtlib

    def test_quoted_symbols_comments_and_strings(self):
        text = '; comment (\n(set-info :source "a (b) ; and ""quote""")\n(minimize |ray_(select a b)|)'
        forms = self.smtlib.parse(text)
        self.assertEqual(forms[1], ['minimize', '|ray_(select a b)|'])
        self.assertEqual(self.smtlib.parse(self.smtlib.render(forms[0])), [forms[0]])

    def test_instrument_preserves_constraints_and_objective(self):
        source = '(set-logic QF_NIA)\n(declare-fun |x (a)| () Int)\n(assert (>= |x (a)| 3))\n(minimize |x (a)|)\n(check-sat)\n(exit)'
        prepared, objective = self.smtlib.optimization_input(source)
        self.assertEqual(objective, '|x (a)|')
        before = self.smtlib.parse(source)
        after = self.smtlib.parse(prepared)
        self.assertEqual(after[:4], before[:4])
        self.assertEqual(after[-4:], [['check-sat'], ['get-objectives'], ['get-value', ['|x (a)|']], ['exit']])
        self.assertEqual(sum(x[0]=='check-sat' for x in after), 1)

    def test_rejects_multiple_objectives_and_malformed_text(self):
        with self.assertRaises(ValueError):
            self.smtlib.optimization_input('(minimize x)(minimize y)(check-sat)')
        with self.assertRaises(ValueError):
            self.smtlib.parse('(assert true')
        with self.assertRaises(ValueError):
            self.smtlib.parse('(assert |unterminated)')

    def test_exact_numbers_and_infinitesimals(self):
        numeric = self.smtlib.numeric_value
        self.assertEqual(numeric('(/ (- 7) 3)'), (0, Fraction(-7,3), 0))
        self.assertEqual(numeric('(+ 3 (* (/ 1 2) epsilon))'), (0,3,Fraction(1,2)))
        self.assertEqual(numeric('(- oo)'), (-1,0,0))
        self.assertEqual(self.smtlib.finite_value('0.125'), Fraction(1,8))
        self.assertIsNone(self.smtlib.finite_value('epsilon'))
        self.assertIsNone(numeric('(root-obj (+ (^ x 2) (- 2)) 2)'))

    def test_strict_improvement_does_not_reset_equal_values(self):
        self.assertTrue(self.smtlib.strictly_better('(/ 1 3)','1'))
        self.assertFalse(self.smtlib.strictly_better('(/ 2 6)','(/ 1 3)'))
        self.assertFalse(self.smtlib.strictly_better('epsilon','1'))


if __name__ == '__main__':
    unittest.main()
