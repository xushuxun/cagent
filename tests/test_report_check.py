import unittest
from typing import cast

from cagent.core.agent import Agent
from cagent.report.check import check_statement


class FakeAgent:
    def chat(self, prompt, **kwargs):
        return """```python
from decimal import Decimal
rows = context['rows']
computed = sum((Decimal(str(row['value'])) for row in rows[:2]), Decimal(0))
checks = [{'target': '资产总计', 'item': rows[2]['item'], 'add': [row['item'] for row in rows[:2]], 'sub': [], 'computed': str(computed), 'reported': str(rows[2]['value']), 'ok': computed == Decimal(str(rows[2]['value']))}]
FINAL_VAR(checks)
```"""


class CheckTest(unittest.TestCase):
    def test_rlm_executes_sum_without_changing_rows(self):
        rows = [{"item": "流动资产", "value": 60}, {"item": "非流动资产", "value": 40}, {"item": "资产总计", "value": 100}]
        original = [row.copy() for row in rows]
        checks = check_statement(cast(Agent, FakeAgent()), rows)
        self.assertTrue(checks[0]["ok"])
        self.assertEqual(checks[0]["computed"], "100")
        self.assertEqual(rows, original)


if __name__ == "__main__":
    unittest.main()
