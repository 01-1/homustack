"""Synthetic v8.0.54 formats only; never uses proxy/API/auth contents."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

for _key in ("USAGE_ALERT_UNITS", "USAGE_ALERT_DIRS", "USAGE_ALERT_MIRROR"):
    os.environ.pop(_key, None)  # test the defaults, not this machine's settings
loader = importlib.machinery.SourceFileLoader("usage_alert", str(Path(__file__).with_name("usage-alert.sh")))
spec = importlib.util.spec_from_loader(loader.name, loader)
u = importlib.util.module_from_spec(spec)
loader.exec_module(u)
A, B = "codex-one@example.com-plus.json", "codex-two@example.com-plus.json"


def failure(account=A, kind="usage_limit_reached", seconds=3600, provider="codex"):
    return ('[2025-11-10 03:00:00] [warn ] [conductor_execution.go:2035] | a1b2c3d4 | '
            f'429 | 1s | upstream execution failed: provider={provider} model=gpt-5 '
            f'auth=provider={provider} auth_file={account} '
            'err=' + json.dumps({"error": {"type": kind, "resets_in_seconds": seconds}}, separators=(",", ":")))


class Tests(unittest.TestCase):
    def setUp(self):
        self.now = 10000
        self.alerts = []
        self.w = u.Watcher(lambda *args: self.alerts.append(args) or True,
                           {"Codex": {A, B}}, lambda: self.now)

    def all_out(self):
        return [a for a in self.alerts if "ALL ACCOUNTS" in a[1]]

    def test_mirror_uses_only_sanitized_event_and_configured_command(self):
        run=Mock(return_value=SimpleNamespace(returncode=0,stdout='queued\n'))
        self.assertTrue(u.mirror_notice('OUT OF USAGE: Codex — ALL ACCOUNTS','Limit resets soon.',run,
                                        command="/opt/relay --producer 'usage alerts'"))
        self.assertEqual(run.call_args.kwargs['input'], 'OUT OF USAGE: Codex — ALL ACCOUNTS\nLimit resets soon.')
        self.assertEqual(run.call_args.args[0], ['/opt/relay', '--producer', 'usage alerts'])

    def test_no_mirror_command_means_no_mirror(self):
        run=Mock(side_effect=AssertionError('must not run'))
        self.assertFalse(u.mirror_notice('OUT OF USAGE: Codex — ALL ACCOUNTS','body',run,command=''))
        self.assertFalse(u.mirror_notice('OUT OF USAGE: Codex — ALL ACCOUNTS','body',run))
        run.assert_not_called()

    def test_provider_settings_parse_and_reject_garbage(self):
        self.assertEqual(u.pairs('a.service=Codex, b.service=Claude'), {'a.service': 'Codex', 'b.service': 'Claude'})
        with self.assertRaises(SystemExit):
            u.pairs('no-equals-sign')
        self.assertEqual(u.PROVIDERS, {'cliproxyapi.service': 'Codex', 'cliproxyapi-claude.service': 'Claude'})
        self.assertEqual(sorted(u.DIRS), ['Claude', 'Codex'])

    def test_category_only_production_gate_keeps_whole_provider_out(self):
        for title in ('OUT OF USAGE: Codex one@example.com',
                      'Usage cooldown: Codex', 'OUT OF USAGE: Claude person@example.com'):
            self.assertFalse(u.category_notice(title))
            run=Mock(side_effect=AssertionError('must not deliver account event'))
            self.assertFalse(u.mirror_notice(title,'sanitized account status',run))
            run.assert_not_called()
        self.assertTrue(u.category_notice('OUT OF USAGE: Codex — ALL ACCOUNTS'))
        self.assertTrue(u.category_notice('OUT OF USAGE: Claude — ALL ACCOUNTS'))

    def test_mirror_failure_cannot_break_desktop_watcher(self):
        self.assertFalse(u.mirror_notice('OUT OF USAGE: Codex — ALL ACCOUNTS','body',Mock(side_effect=OSError('offline')),command='relay'))
        self.assertFalse(u.mirror_notice('OUT OF USAGE: Codex — ALL ACCOUNTS','body',Mock(return_value=SimpleNamespace(returncode=1,stdout='')),command='relay'))

    def test_one_down_not_all(self):
        self.w.line(failure(), "Codex")
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(self.all_out())

    def test_unknown_account(self):
        self.w.line(failure("unknown.json"), "Codex")
        self.w.line(failure(B), "Codex")
        self.assertFalse(self.all_out())
        self.assertNotIn(("Codex", "unknown.json"), self.w.states)

    def test_expired_cooldown(self):
        self.w.line(failure(seconds=3), "Codex")
        self.now += 4
        self.w.line(failure(B), "Codex")
        self.assertFalse(self.all_out())
        self.assertNotIn(("Codex", A), self.w.states)

    def test_stale_evidence(self):
        self.w.line(failure(), "Codex")
        self.now += 601
        self.w.line(failure(B), "Codex")
        self.assertFalse(self.all_out())

    def test_success_recovery(self):
        self.w.line(failure(), "Codex")
        self.w.recover("Codex", A)
        self.w.line(failure(B), "Codex")
        self.assertFalse(self.all_out())
        self.w.line(failure(), "Codex")
        self.assertEqual(len(self.all_out()), 1)

    def test_true_all_out_and_debounce(self):
        for _ in range(3):
            self.w.line(failure(), "Codex")
            self.w.line(failure(B), "Codex")
        self.assertEqual(len(self.alerts), 3)
        self.assertEqual(len(self.all_out()), 1)
        self.now += 601
        self.w.line(failure(), "Codex")
        self.assertEqual(len(self.alerts), 4)
        self.assertEqual(len(self.all_out()), 1)

    def test_transient_not_critical_or_all(self):
        self.w.line(failure(kind="rate_limit_error"), "Codex")
        self.w.line(failure(B, kind="rate_limit_exceeded"), "Codex")
        self.assertTrue(all(a[0] == "normal" for a in self.alerts))
        self.assertFalse(self.all_out())

    def test_ambiguous_removes_proof(self):
        self.w.line(failure(), "Codex")
        self.w.line(failure(kind="rate_limit_error"), "Codex")
        self.w.line(failure(B), "Codex")
        self.assertFalse(self.all_out())

    def test_unattributed_success_preserves_account_debounce(self):
        self.w.line(failure(), "Codex")
        self.w.recover("Codex")
        self.w.line(failure(), "Codex")
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(self.all_out())

    def test_general_unified_rejection_and_overage(self):
        body = '{"error":{"type":"rate_limit_error"}}'
        self.w.line(body, "Claude", {"anthropic-ratelimit-unified-status": "rejected"})
        self.assertEqual(self.alerts[-1][0], "critical")
        self.alerts.clear()
        self.w.line(body, "Claude", {"anthropic-ratelimit-unified-status": "rejected",
                                    "anthropic-ratelimit-unified-overage-status": "rejected"})
        self.assertEqual(self.alerts[-1][0], "normal")

    def test_missing_reset_not_all(self):
        self.w.line(failure().replace(',"resets_in_seconds":3600', ''), "Codex")
        self.w.line(failure(B), "Codex")
        self.assertFalse(self.all_out())

    def test_no_secrets(self):
        self.w.line(failure() + ' api_key=sk-TESTSECRET token=BEARERSECRET', "Codex")
        output = str(self.alerts)
        self.assertNotIn("TESTSECRET", output)
        self.assertNotIn("BEARERSECRET", output)
        self.assertEqual(self.alerts[0][1], "OUT OF USAGE: Codex one@example.com")

    def test_codex_account_names_with_recorded_plan_suffixes(self):
        for plan in ("prolite", "edu-plus"):
            with self.subTest(plan=plan):
                alerts = []
                account = f"codex-person@example.com-{plan}.json"
                watcher = u.Watcher(lambda *args: alerts.append(args) or True,
                                    {"Codex": {account}}, lambda: self.now)
                watcher.line(failure(account), "Codex")
                self.assertEqual(alerts[0][1], "OUT OF USAGE: Codex person@example.com")

    def test_harmless(self):
        for line in ('200 | request complete', 'Use OAuth provider=codex auth_file=x.json',
                     'quota-exceeded: switch-project: true', '{"usage":{"total_tokens":429}}',
                     '{"error":{"type":"authentication_error"}}',
                     '{"choices":[{"message":{"content":"error rate_limit_error 429"}}]}'):
            self.w.line(line, "Codex")
        self.assertFalse(self.alerts)

    def test_model_cooldown_is_not_provider_exhaustion(self):
        self.w.line('{"error":{"code":"model_cooldown","message":"All credentials for model gpt-5 are cooling down via provider codex","reset_seconds":30}}', "Codex")
        self.assertEqual(self.alerts[0][0], "normal")
        self.assertFalse(self.all_out())

    def test_claude_shared_cap_and_transient(self):
        body = '{"type":"error","error":{"type":"rate_limit_error","message":"Rate limit exceeded"}}'
        self.w.line(body, "Claude")
        self.w.line(body, "Claude", {"anthropic-ratelimit-unified-5h-status": "rejected",
                                    "anthropic-ratelimit-unified-5h-reset": "1234567890"})
        self.assertEqual([a[0] for a in self.alerts], ["normal", "critical"])
        self.assertFalse(self.all_out())

    def test_inventory_names_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / A).mkdir()  # A directory cannot be opened as an auth document.
            (root / "readme.txt").touch()
            self.assertEqual(u.inventory({"Codex": root}), {"Codex": {A}})

    def test_files_skip_history_requests_recover_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.log").write_text(failure() + '\n')
            files = u.LogFiles({"Codex": root}, self.w)
            files.scan()
            self.assertFalse(self.alerts)
            with (root / "main.log").open("a") as stream:
                stream.write(failure() + '\n')
            files.scan()
            self.assertEqual(len(self.alerts), 1)
            (root / "error-test.log").write_text('=== REQUEST ===\n' + failure(B) + '\n=== RESPONSE ===\nStatus: 200\n{}\n')
            files.scan()
            self.assertFalse(self.w.states)
            self.assertFalse(self.all_out())
            # Claude final response headers contain shared subscription rejection.
            (root / "error-second.log").write_text('=== RESPONSE ===\nStatus: 429\nAnthropic-Ratelimit-Unified-5h-Status: rejected\nAnthropic-Ratelimit-Unified-5h-Reset: 1234567890\n{"error":{"type":"rate_limit_error"}}\n')
            other = u.LogFiles({"Claude": root}, self.w)
            (root / "error-third.log").write_text((root / "error-second.log").read_text())
            other.scan()
            self.assertEqual(self.alerts[-1][0], "critical")


if __name__ == "__main__":
    unittest.main()
