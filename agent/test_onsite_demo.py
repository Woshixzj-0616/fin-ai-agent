import subprocess
import sys
import unittest
from pathlib import Path

import main as cli_main

import onsite_demo


ROOT = Path(__file__).resolve().parents[1]


class OnsiteBundleTests(unittest.TestCase):
    def test_bundle_contains_four_required_claim_outcomes(self):
        report = onsite_demo.inspect_bundle(ROOT)

        self.assertTrue(report["ready"], report["problems"])
        self.assertEqual(report["claim_count"], 8)
        self.assertEqual(
            report["claim_outcomes"],
            {
                "missing_evidence": 1,
                "scope_conflict": 1,
                "supported": 3,
                "wrong_value": 2,
            },
        )
        self.assertGreaterEqual(report["material_count"], 5)

    def test_failure_playbook_covers_required_offline_drills(self):
        drills = onsite_demo.failure_playbook()

        self.assertEqual(
            [drill["id"] for drill in drills],
            ["network_failure", "model_timeout", "material_format_error"],
        )
        self.assertTrue(all(drill["offline_only"] for drill in drills))
        self.assertTrue(all(drill["visible_signal"] for drill in drills))

    def test_cli_demo_uses_isolated_run_root(self):
        command = onsite_demo.cli_demo_command(
            sys.executable,
            ROOT,
            ROOT / "work" / "p4-onsite-cli",
        )

        self.assertEqual(command[:3], [sys.executable, "-B", "agent/main.py"])
        run_root_index = command.index("--run-root")
        self.assertEqual(command[run_root_index + 1], str(ROOT / "work" / "p4-onsite-cli"))
        self.assertLess(run_root_index, command.index("demo"))
        self.assertIn("--render", command)

    def test_cli_launcher_runs_demo_without_starting_web(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        result = onsite_demo.run_launcher(
            mode="cli",
            host="127.0.0.1",
            port=8765,
            root=ROOT,
            python=sys.executable,
            run_root=ROOT / "work" / "p4-onsite-cli",
            runner=fake_runner,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("demo", calls[0][0])
        self.assertEqual(calls[0][1]["cwd"], ROOT)

    def test_cli_parser_accepts_run_root_before_command(self):
        args = cli_main.parser().parse_args([
            "--run-root",
            str(ROOT / "work" / "isolated"),
            "demo",
            "--render",
        ])

        self.assertEqual(args.run_root, ROOT / "work" / "isolated")
        self.assertEqual(args.command, "demo")
        self.assertTrue(args.render)


if __name__ == "__main__":
    unittest.main()
