import re
import unittest
from decimal import Decimal
from pathlib import Path

from cagent.report.source import _amount, grid_pages, materialize_rows, parse_tables, verify_rows


class AmountTest(unittest.TestCase):
    def test_financial_formats(self):
        cases = {
            "(123)": Decimal(-123),
            "(1,234.56)": Decimal("-1234.56"),
            "1,234.56": Decimal("1234.56"),
            "-123": Decimal(-123),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_amount(raw), expected)
        for raw in ("", "-", "—", "--", "项目"):
            with self.subTest(raw=raw):
                self.assertIsNone(_amount(raw))


class GridTest(unittest.TestCase):
    def setUp(self):
        self.pages = ['<table><tr><td>项目</td><td>2024</td><td>2023</td></tr><tr><td>营业收入</td><td>(1,200.50)</td><td>900</td></tr></table>']
        self.selection = {"item": "营业收入", "note": "", "page": 8, "table": 0, "item_cell": "r1c0", "value_cell": "r1c1"}

    def test_grid_has_programmatic_coordinates(self):
        grid = grid_pages(self.pages, 8)
        self.assertIn('<page index="8">', grid)
        self.assertIn("r1c1=(1,200.50)", grid)

    def test_materialize_reads_value_and_raw_value(self):
        rows = materialize_rows([self.selection], self.pages, 8, {(8, 0): 1})
        self.assertEqual(rows[0]["value"], -1200.5)
        self.assertEqual(rows[0]["raw_value"], "(1,200.50)")
        self.assertEqual(verify_rows(rows, self.pages, 8), [])

    def test_rejects_prior_year_column(self):
        with self.assertRaisesRegex(ValueError, "唯一当期列"):
            materialize_rows([self.selection], self.pages, 8, {(8, 0): 2})

    def test_missing_cell_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "单元格不存在"):
            materialize_rows([{**self.selection, "value_cell": "r9c9"}], self.pages, 8)


class RealAnnualReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "fixtures" / "002594-2024.md"
        cls.pages = re.split(r"<!-- page \d+ -->", path.read_text(encoding="utf-8"))[1:]

    def test_balance_sheet_cash_cell(self):
        tables = parse_tables(self.pages[141])
        self.assertEqual(tables[0][2][2], "102,738,734")
        rows = materialize_rows(
            [{"item": "货币资金", "note": "1", "page": 142, "table": 0, "item_cell": "r2c0", "value_cell": "r2c2"}],
            [self.pages[141]],
            142,
            {(142, 0): 2},
        )
        self.assertEqual(rows[0]["value"], 102738734)
        self.assertEqual(rows[0]["raw_value"], "102,738,734")
        self.assertEqual(verify_rows(rows, [self.pages[141]], 142), [])

    def test_income_statement_negative_cell(self):
        tables = parse_tables(self.pages[144])
        source_row = next(index for index, row in enumerate(tables[0]) if row[0] == "信用减值损失")
        rows = materialize_rows(
            [{"item": "信用减值损失", "note": "53", "page": 145, "table": 0, "item_cell": f"r{source_row}c0", "value_cell": f"r{source_row}c2"}],
            [self.pages[144]],
            145,
            {(145, 0): 2},
        )
        self.assertEqual(rows[0]["value"], -1553315)
        self.assertEqual(rows[0]["raw_value"], "(1,553,315)")


if __name__ == "__main__":
    unittest.main()
