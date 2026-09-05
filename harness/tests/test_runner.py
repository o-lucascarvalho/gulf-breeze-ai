from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness.runner.runner import load_scenario_file, run_scenario, write_result


class HarnessRunnerTests(unittest.TestCase):
    def test_fake_mode_runs_and_writes_results(self) -> None:
        scenario = load_scenario_file(Path("harness/scenarios/sample_balance_lookup.yaml"))
        result = run_scenario(scenario, mode="fake")

        self.assertTrue(result.passed)
        self.assertEqual(result.scenario, "sample_balance_lookup")
        self.assertEqual(result.turns[0].session_id, 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "result.json"
            write_result(result, output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["passed"])
        self.assertEqual(payload["turns"][0]["response"], "Tamara Cruz currently owes $9,680.04.")

    def test_assertion_failures_are_reported(self) -> None:
        scenario = load_scenario_file(Path("harness/scenarios/sample_balance_lookup.yaml"))
        broken_turn = scenario.turns[0]
        bad_scenario = scenario.__class__(
            name=scenario.name,
            mode=scenario.mode,
            tools=scenario.tools,
            turns=[broken_turn.__class__(user=broken_turn.user, script=broken_turn.script, assertions=[{"type": "response_contains", "value": "not present"}], reuse_session=True)],
        )

        result = run_scenario(bad_scenario, mode="fake")

        self.assertFalse(result.passed)
        self.assertIn("response did not contain", result.failures[0])


if __name__ == "__main__":
    unittest.main()