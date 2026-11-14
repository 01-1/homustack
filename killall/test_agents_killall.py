"""Tests use fake process tables, a fake /proc and mocked commands. Nothing here can
signal, stop or disable a real process or unit: main --yes only runs with every
command and signal path replaced, and ka only ever reaches an inert stub."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import pty
import pwd
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import call, patch

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('agents_killall', str(HERE / 'agents-killall'))
spec = importlib.util.spec_from_loader(loader.name, loader)
k = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = k
loader.exec_module(k)

SCOPE = '0::/user.slice/user@1000.service/app.slice/agent-session-ab.scope\n'
TERMINAL = '0::/user.slice/user@1000.service/app.slice/app-terminal-1.scope\n'
UNIT = '0::/user.slice/user@1000.service/app.slice/agent-waker.service\n'

CONFIG = {
    'grace_seconds': 5,
    'first_units': ['agent-watchdog.service'],
    'preserve': {'names': ['usage-meter']},
    'group': [
        {'name': 'agents', 'names': ['claude', 'codex'], 'cgroups': [r'/agent-session-[^/]*\.scope(?:/|$)'],
         'units': ['agent-session-*.scope', 'agent-waker.*']},
        {'name': 'app', 'names': ['agent-app'], 'units': ['app-agent-app-*.scope']},
        {'name': 'proxies', 'optional': True, 'names': ['llm-proxy'], 'units': ['llm-proxy.service'],
         'unit_action': 'stop'},
    ],
}


def cfg(**changes):
    return k.parse_config({**CONFIG, **changes})


def proc(pid, ppid, name, argv=None, start='100', cgroup='', exe=None):
    return k.Process(pid, ppid, start, name, '/usr/bin/' + name if exe is None else exe,
                     tuple(argv or [name]), 'S', cgroup)


class Config(unittest.TestCase):
    def test_example_config_loads(self):
        c = k.load_config(HERE / 'config.example.toml')
        self.assertEqual([g.name for g in c.groups], ['agents', 'app', 'proxies'])
        self.assertEqual(k.active_groups(c), {'agents', 'app'})
        self.assertEqual(k.active_groups(c, include=['proxies'], keep=['app']), {'agents', 'proxies'})
        self.assertEqual(c.ka_open, k.KaOpen('Agent Recovery', (), ('app',)))

    def test_invalid_configs_are_rejected(self):
        bad = [
            {**CONFIG, 'surprise': 1},
            {**CONFIG, 'group': []},
            {**CONFIG, 'group': [{'name': 'a', 'names': ['x'], 'kill_everything': True}]},
            {**CONFIG, 'group': [{'name': 'a', 'cgroups': ['(']}]},
            {**CONFIG, 'group': [{'name': 'a', 'names': ['x'], 'unit_action': 'mask'}]},
            {**CONFIG, 'group': [{'name': 'a'}]},
            {**CONFIG, 'group': [{'name': 'a', 'names': ['x']}, {'name': 'a', 'names': ['y']}]},
            {**CONFIG, 'group': [{'name': 'a', 'child_names': ['x'], 'parents': ['nope']}]},
            {**CONFIG, 'group': [{'name': 'a', 'child_names': ['x']}]},
            {**CONFIG, 'grace_seconds': 0},
            {**CONFIG, 'first_units': ['not a unit']},
            # A string where a list belongs must not become one-letter names or patterns.
            {**CONFIG, 'group': [{'name': 'a', 'names': 'claude'}]},
            {**CONFIG, 'group': [{'name': 'a', 'names': ['x'], 'units': 'agent-*.scope'}]},
            {**CONFIG, 'group': [{'name': 'a', 'names': ['sh']}]},
            {**CONFIG, 'group': [{'name': 'a', 'names': ['claude'], 'child_names': ['cl'], 'parents': ['a']}]},
            {**CONFIG, 'first_units': 'agent-watchdog.service'},
            {**CONFIG, 'ka_open': {}},
            {**CONFIG, 'ka_open': {'recovery_name': 'R', 'surprise': 1}},
            {**CONFIG, 'ka_open': {'recovery_name': '--yes'}},
            {**CONFIG, 'ka_open': {'recovery_name': ''}},
            {**CONFIG, 'ka_open': {'recovery_name': 'R', 'keep': ['typo']}},
            {**CONFIG, 'ka_open': {'gate': 'check-stop'}},
            {**CONFIG, 'ka_open': {'gate': ['check-stop']}},  # bare names are looked up on PATH
            {**CONFIG, 'ka_open': 'Agent Recovery'},
        ]
        for data in bad:
            with self.assertRaises(ValueError, msg=data):
                k.parse_config(data)

    def test_broad_unit_patterns_are_rejected(self):
        for pattern in ['*', '*.service', '*.*', 'a*.scope', '[a-z]*.timer', '?*-*.scope', '*service',
                        '*.service*', '*[.]service', '*ervice', 'app-*', '*-agent-*.scope', 'agent-waker']:
            with self.assertRaises(ValueError, msg=pattern):
                cfg(group=[{'name': 'a', 'names': ['claude'], 'units': [pattern]}])
        for pattern in ['agent-session-*.scope', 'agent-waker.*', 'app-*.scope', 'llm-proxy.service']:
            cfg(group=[{'name': 'a', 'names': ['claude'], 'units': [pattern]}])

    def test_ka_open_gate_is_expanded_and_optional(self):
        home = pwd.getpwuid(os.getuid()).pw_dir
        with patch.dict(os.environ, {'HOME': '/tmp/elsewhere'}):
            c = cfg(ka_open={'gate': ['~/bin/check-stop', '--strict']})
        self.assertEqual(c.ka_open.gate, (home + '/bin/check-stop', '--strict'))
        self.assertIsNone(c.ka_open.recovery_name)
        self.assertEqual(cfg(ka_open={'recovery_name': 'R'}).ka_open, k.KaOpen('R', (), ()))

    def test_config_path_ignores_home_and_xdg(self):
        with patch.dict(os.environ, {'HOME': '/tmp/elsewhere', 'XDG_CONFIG_HOME': '/tmp/elsewhere'}):
            fresh_loader = importlib.machinery.SourceFileLoader('agents_killall_env', str(HERE / 'agents-killall'))
            fresh = importlib.util.module_from_spec(importlib.util.spec_from_loader(fresh_loader.name, fresh_loader))
            sys.modules[fresh_loader.name] = fresh
            fresh_loader.exec_module(fresh)
        expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.config/agents-killall/config.toml'
        self.assertEqual((fresh.DEFAULT_CONFIG, fresh.OPEN_CONFIG), (expected, expected))
        self.assertNotIn('/tmp/elsewhere', str(fresh.GATE_ENV))

    def test_unknown_group_in_options_is_rejected(self):
        with self.assertRaises(ValueError):
            k.active_groups(cfg(), keep=['typo'])


class Selection(unittest.TestCase):
    def test_names_cgroups_and_descendants(self):
        ps = {10: proc(10, 1, 'claude'), 11: proc(11, 10, 'bash'), 12: proc(12, 11, 'python3'),
              20: proc(20, 1, 'helper', cgroup=SCOPE), 30: proc(30, 1, 'firefox'),
              40: proc(40, 1, 'agent-app')}
        self.assertEqual(k.targets(ps, cfg(), {'agents', 'app'}),
                         {10: 'agents', 11: 'child', 12: 'child', 20: 'agents', 40: 'app'})

    def test_prompt_text_and_arguments_never_match(self):
        for p in [proc(2, 1, 'bash', ['bash', '-c', 'claude -p secret']),
                  proc(2, 1, 'firefox', ['firefox', 'claude']),
                  proc(2, 1, 'echo', ['echo', 'codex'])]:
            self.assertIsNone(k.classify(p, cfg()))
        script = proc(2, 1, 'node', ['node', '/opt/bin/claude', '-p', 'x'])
        self.assertEqual(k.classify(script, cfg()), 'agents')
        # Fedora's node is /usr/bin/node-22.
        versioned = proc(2, 1, 'node', ['node', '/usr/local/bin/codex'], exe='/usr/bin/node-22')
        self.assertEqual(k.classify(versioned, cfg()), 'agents')

    def test_preserved_processes_and_their_children_survive(self):
        ps = {10: proc(10, 1, 'claude'), 11: proc(11, 10, 'python3', ['python3', '/bin/usage-meter']),
              12: proc(12, 11, 'notify-send')}
        self.assertEqual(set(k.targets(ps, cfg(), {'agents', 'app'})), {10})

    def test_kept_and_optional_groups_are_not_swept_in_as_children(self):
        ps = {10: proc(10, 1, 'agent-app'), 11: proc(11, 10, 'llm-proxy'), 12: proc(12, 10, 'claude')}
        c = cfg()
        self.assertEqual(set(k.targets(ps, c, k.active_groups(c))), {10, 12})
        self.assertEqual(set(k.targets(ps, c, k.active_groups(c, keep=['app']))), {12})
        self.assertEqual(set(k.targets(ps, c, k.active_groups(c, include=['proxies']))), {10, 11, 12})

    def test_only_in_cgroups_limits_name_matches(self):
        group = {**CONFIG['group'][0], 'only_in_cgroups': [r'/agent-session-'], 'cgroups': []}
        c = cfg(group=[group])
        ps = {10: proc(10, 1, 'claude', cgroup=TERMINAL), 11: proc(11, 1, 'claude', cgroup=SCOPE)}
        self.assertEqual(set(k.targets(ps, c, {'agents'})), {11})

    def test_child_names_under_a_kept_parent(self):
        groups = [{'name': 'app', 'names': ['agent-app']},
                  {'name': 'agents', 'only_in_cgroups': ['/agent-session-'], 'names': ['claude'],
                   'child_names': ['claude'], 'parents': ['app']}]
        c = cfg(group=groups)
        ps = {10: proc(10, 1, 'agent-app'), 11: proc(11, 10, 'claude'), 12: proc(12, 1, 'claude'),
              13: proc(13, 11, 'bash')}
        self.assertEqual(k.targets(ps, c, {'agents'}), {11: 'agents', 13: 'child'})

    def test_recovery_agent_is_found_by_its_exact_name_argument(self):
        recovery = proc(5, 1, 'claude', ['claude', '--name', 'Agent Recovery', '-p', 'x'])
        ps = {5: recovery, 6: proc(6, 5, 'bash'), 7: proc(7, 6, 'python3')}
        self.assertTrue(k.beneath_recovery(ps, 7, 'Agent Recovery'))
        self.assertTrue(k.is_recovery(proc(5, 1, 'claude', ['claude', '--name=Agent Recovery']), 'Agent Recovery'))
        for argv in (['claude', '--name', 'Agent Recovery 2'], ['claude', 'Agent Recovery', '--name', 'x'],
                     ['claude', '-p', '--name Agent Recovery']):
            self.assertFalse(k.is_recovery(proc(5, 1, 'claude', argv), 'Agent Recovery'), argv)
        # The process itself never counts, only its ancestors.
        self.assertFalse(k.beneath_recovery({5: recovery}, 5, 'Agent Recovery'))

    def test_open_gate_exit_status_decides(self):
        self.assertTrue(k.open_gate_passes(['/usr/bin/true']))
        self.assertFalse(k.open_gate_passes(['/usr/bin/false']))
        self.assertFalse(k.open_gate_passes(['/nonexistent/check-stop']))

    def test_open_gate_gets_a_fixed_environment(self):
        with patch.dict(os.environ, {'PATH': '/tmp/evil', 'LD_PRELOAD': '/tmp/evil.so'}), \
             patch.object(k.subprocess, 'run') as run:
            run.return_value.returncode = 0
            k.open_gate_passes(['/usr/local/bin/check-stop'])
        env = run.call_args.kwargs['env']
        self.assertEqual(set(env), {'PATH', 'HOME', 'LANG'})
        self.assertEqual(env['PATH'], '/usr/bin:/bin')

    def test_never_selects_itself(self):
        me = proc(os.getpid(), 1, 'claude')
        self.assertEqual(k.targets({me.pid: me}, cfg(), {'agents'}), {})


class FakeProc(unittest.TestCase):
    def make(self, root, pid, uid, name='claude', exe=True):
        d = root / str(pid)
        d.mkdir()
        (d / 'status').write_text(f'Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')
        (d / 'stat').write_text(f'{pid} ({name} x) S 1 ' + '0 ' * 17 + '777 0\n')
        (d / 'comm').write_text(name + '\n')
        (d / 'cmdline').write_bytes(name.encode() + b'\0--secret\0')
        (d / 'cgroup').write_text(SCOPE)
        if exe:
            (d / 'exe').symlink_to('/usr/bin/' + name)

    def test_ppid_survives_a_comm_with_spaces_and_parentheses(self):
        uid = os.getuid()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, 5, uid, name='a) S 9 (b')
            self.assertEqual(k.read_process(5, root).ppid, 1)

    def test_reads_own_including_non_dumpable_and_skips_other_users(self):
        uid = os.getuid()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, 5, uid)
            self.make(root, 6, uid + 1)
            self.make(root, 7, uid, exe=False)  # exe unreadable, like a non-dumpable process
            found = k.processes(root)
        self.assertEqual(set(found), {5, 7})
        self.assertEqual((found[5].ppid, found[5].start, found[5].comm), (1, '777', 'claude'))
        self.assertEqual(found[7].exe, '')
        self.assertEqual(k.classify(found[7], cfg()), 'agents')

    def test_setuid_process_is_not_ours(self):
        uid = os.getuid()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, 5, uid)
            (root / '5/status').write_text(f'Uid:\t{uid}\t0\t0\t0\n')
            self.assertIsNone(k.read_process(5, root))


class Signals(unittest.TestCase):
    def test_pid_reuse_is_not_signalled(self):
        p = proc(2, 1, 'claude')
        with patch.object(k.os, 'pidfd_open', return_value=55), patch.object(k.os, 'close'), \
             patch.object(k, 'read_process', return_value=proc(2, 1, 'firefox', start='999')), \
             patch.object(k.signal, 'pidfd_send_signal') as send:
            k.signal_process(p, k.signal.SIGTERM)
        send.assert_not_called()

    def test_unpermitted_signal_does_not_abort_the_stop(self):
        p = proc(2, 1, 'claude')
        with patch.object(k.os, 'pidfd_open', return_value=55), patch.object(k.os, 'close'), \
             patch.object(k, 'read_process', return_value=p), \
             patch.object(k.signal, 'pidfd_send_signal', side_effect=PermissionError(1, 'EPERM')):
            k.signal_process(p, k.signal.SIGTERM)
        with patch.object(k.os, 'pidfd_open', side_effect=OSError(24, 'EMFILE')):
            k.signal_process(p, k.signal.SIGTERM)

    def test_signals_the_pinned_identity(self):
        p = proc(2, 1, 'claude')
        with patch.object(k.os, 'pidfd_open', return_value=55), patch.object(k.os, 'close'), \
             patch.object(k, 'read_process', return_value=p), \
             patch.object(k.signal, 'pidfd_send_signal') as send:
            k.signal_process(p, k.signal.SIGTERM)
        send.assert_called_once_with(55, k.signal.SIGTERM)

    def test_orphan_is_killed_after_its_parent_exits(self):
        root, child, orphan = proc(2, 1, 'claude'), proc(3, 2, 'worker'), proc(3, 1, 'worker')
        with patch.object(k, 'processes', return_value={3: orphan}), \
             patch.object(k, 'signal_process') as send, patch.object(k.time, 'sleep'), \
             patch.object(k.time, 'monotonic', side_effect=[0, 0, 6]):
            k.stop_processes(cfg(), {'agents'}, {2: root, 3: child})
        self.assertIn(call(child, k.signal.SIGKILL), send.call_args_list)
        self.assertNotIn(call(root, k.signal.SIGKILL), send.call_args_list)


class Units(unittest.TestCase):
    FOUND = {'agent-watchdog.service', 'agent-waker.timer', 'agent-waker.service', 'agent-session-ab.scope',
             'app-agent-app-1.scope', 'llm-proxy.service', 'backup.timer'}

    def test_plan_order_actions_and_kept_groups(self):
        c = cfg()
        self.assertEqual(list(k.unit_plan(self.FOUND, c, {'agents', 'app'}).items()),
                         [('agent-watchdog.service', 'disable'), ('agent-waker.timer', 'disable'),
                          ('agent-waker.service', 'disable'), ('agent-session-ab.scope', 'stop'),
                          ('app-agent-app-1.scope', 'stop')])
        plan = k.unit_plan(self.FOUND, c, k.active_groups(c, include=['proxies'], keep=['app']))
        self.assertNotIn('app-agent-app-1.scope', plan)
        self.assertEqual(plan['llm-proxy.service'], 'stop')
        self.assertNotIn('backup.timer', plan)

    def test_first_units_only_when_present(self):
        self.assertNotIn('agent-watchdog.service', k.unit_plan({'agent-waker.timer'}, cfg(), {'agents'}))

    def test_first_unit_scope_is_stopped_not_disabled(self):
        plan = k.unit_plan({'agent-session-ab.scope'}, cfg(first_units=['agent-session-ab.scope']), {'agents'})
        self.assertEqual(plan, {'agent-session-ab.scope': 'stop'})

    def test_kept_pattern_wins_over_overlap(self):
        groups = [{'name': 'agents', 'names': ['claude'], 'units': ['app-*.scope']},
                  {'name': 'app', 'names': ['agent-app'], 'units': ['app-agent-*.scope']}]
        plan = k.unit_plan({'app-agent-app-1.scope', 'app-x-1.scope'}, cfg(group=groups), {'agents'})
        self.assertEqual(set(plan), {'app-x-1.scope'})

    def test_vanished_scope_is_tolerated_but_running_unit_is_an_error(self):
        with patch.object(k, 'run', side_effect=[RuntimeError('systemctl exited 5'),
                                                'LoadState=not-found\nActiveState=inactive\n']):
            k.stop_unit('agent-session-ab.scope', 'stop')
        with patch.object(k, 'run', side_effect=[RuntimeError('systemctl exited 1'),
                                                'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n']):
            with self.assertRaises(RuntimeError):
                k.stop_unit('agent-waker.service', 'disable')

    def test_stop_action_leaves_enablement(self):
        with patch.object(k, 'run') as run:
            k.stop_unit('llm-proxy.service', 'stop')
        run.assert_called_once_with(['systemctl', '--user', 'stop', 'llm-proxy.service'])
        self.assertTrue(k.settled({'LoadState': 'loaded', 'ActiveState': 'inactive', 'UnitFileState': 'enabled'}, 'stop'))
        self.assertFalse(k.settled({'LoadState': 'loaded', 'ActiveState': 'inactive', 'UnitFileState': 'enabled'}, 'disable'))


class Main(unittest.TestCase):
    """main with every command, signal and unit path replaced by fakes."""

    def run_main(self, argv, me, found=(), failing=(), tables=None, inventory_error=None,
                 config_text=None, gate=None, extra=None):
        agent = proc(13, 1, 'claude')
        tables = list(tables or [{50: me, 13: agent, **(extra or {})}, {50: me, **(extra or {})}])
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if any(unit in command for unit in failing):
                raise RuntimeError('systemctl exited 1')
            if command[2] == 'show':
                return 'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n'
            return ''

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            shutil.copy(HERE / 'config.example.toml', config)
            if config_text is not None:
                config.write_text(config_text)
            if '--open' not in argv:
                argv = ['--config', str(config), *argv]
            with patch.object(k, 'OPEN_CONFIG', config), \
                 patch.object(k, 'open_gate_passes', side_effect=gate or AssertionError('no gate')), \
                 patch.object(k.os, 'geteuid', return_value=1000), \
                 patch.object(k.os, 'getpid', return_value=50), \
                 patch.object(k, 'processes', side_effect=lambda: tables.pop(0) if len(tables) > 1 else tables[0]), \
                 patch.object(k, 'list_units', return_value=set(found), side_effect=inventory_error), \
                 patch.object(k, 'run', side_effect=fake_run), \
                 patch.object(k, 'stop_processes') as stop, \
                 patch.object(k, 'verify', return_value=[]), \
                 contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                code = k.main(argv)
        return code, commands, stop, out.getvalue(), err.getvalue()

    def test_default_is_a_dry_run(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, out, _ = self.run_main([], me, found={'agent-waker.timer'})
        self.assertEqual(code, 0)
        self.assertEqual(commands, [])
        stop.assert_not_called()
        self.assertIn('DRY RUN', out)
        self.assertIn('13 1 agents S', out)

    def test_root_is_refused(self):
        with patch.object(k.os, 'geteuid', return_value=0), contextlib.redirect_stderr(io.StringIO()), \
             patch.object(k, 'processes', side_effect=AssertionError('no inspection as root')):
            self.assertEqual(k.main(['--yes']), 2)

    def test_refused_beneath_a_target(self):
        me = proc(50, 13, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(['--yes'], me, found={'agent-waker.timer'})
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('beneath', err)

    def test_refused_when_detached_inside_a_target_cgroup(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=SCOPE)
        code, commands, stop, _, err = self.run_main(['--yes'], me)
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('cgroup', err)

    def test_refused_inside_a_unit_it_would_stop(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=UNIT)
        code, commands, stop, _, err = self.run_main(['--yes'], me, found={'agent-waker.service'})
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('unit it would stop', err)

    def test_unit_failures_do_not_prevent_signalling(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        found = {'agent-watchdog.service', 'agent-waker.timer', 'agent-session-ab.scope', 'agent-session-cd.scope'}
        code, commands, stop, _, err = self.run_main(['--yes'], me, found=found,
                                                     failing={'agent-watchdog.service', 'agent-session-ab.scope'})
        self.assertEqual(code, 1)
        stop.assert_called_once()
        self.assertIn(['systemctl', '--user', 'disable', '--now', 'agent-waker.timer'], commands)
        self.assertIn(['systemctl', '--user', 'stop', 'agent-session-cd.scope'], commands)
        self.assertIn('agent-watchdog.service', err)

    def test_clean_run_exits_zero(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, _ = self.run_main(['--yes'], me, found={'agent-watchdog.service'})
        self.assertEqual(code, 0)
        self.assertEqual(commands, [['systemctl', '--user', 'disable', '--now', 'agent-watchdog.service']])
        stop.assert_called_once()

    def test_unit_listing_failure_still_signals_and_tries_first_units(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(
            ['--yes'], me, inventory_error=RuntimeError('systemctl failed (TimeoutExpired)'))
        self.assertEqual(code, 1)
        stop.assert_called_once()
        self.assertEqual(commands, [['systemctl', '--user', 'disable', '--now', 'agent-watchdog.service']])
        self.assertIn('unit inventory', err)

    def test_unit_listing_failure_still_refuses_from_a_target_cgroup(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=SCOPE)
        code, commands, stop, _, _ = self.run_main(['--yes'], me, inventory_error=RuntimeError('systemctl exited 1'))
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()

    def test_dry_run_still_fails_on_unit_listing_error(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        with self.assertRaises(RuntimeError):
            self.run_main([], me, inventory_error=RuntimeError('systemctl exited 1'))

    # A recovery agent the config does not select (e.g. agents limited by only_in_cgroups).
    RECOVERY = {5: proc(5, 1, 'recovery', ['recovery', '--name', 'Agent Recovery'], cgroup=TERMINAL)}

    def test_open_spares_a_recovery_agent_its_groups_would_select(self):
        me = proc(50, 5, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        recovery = {5: proc(5, 1, 'claude', ['claude', '--name', 'Agent Recovery'], cgroup=TERMINAL)}
        code, _, stop, out, _ = self.run_main(['--open'], me, extra=recovery)
        self.assertEqual(code, 0)
        self.assertNotIn('  5 1 ', out)
        self.assertIn('  13 1 agents S', out)
        spared = stop.call_args.args[0]
        self.assertEqual(set(k.targets({**recovery, 6: proc(6, 5, 'bash'), 13: proc(13, 1, 'claude')},
                                       spared, {'agents'})), {13})
        # Its descendants stay too, even ones a group selects by name.
        tree = {**recovery, 6: proc(6, 5, 'bash'), 7: proc(7, 6, 'codex'), 8: proc(8, 7, 'node'),
                13: proc(13, 1, 'claude')}
        self.assertEqual(set(k.targets(tree, spared, {'agents'})), {13})
        # Plain stops don't spare it.
        self.assertIn(5, k.targets(recovery, cfg(), {'agents'}))
        self.assertEqual(set(k.targets(tree, cfg(), {'agents'})), {5, 6, 7, 8, 13})

    def test_open_from_the_recovery_agent_keeps_the_app(self):
        me = proc(50, 5, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        found = {'agent-waker.timer', 'app-my-agent-app-1.scope'}
        code, commands, stop, out, _ = self.run_main(['--open'], me, found=found, extra=self.RECOVERY)
        self.assertEqual(code, 0)
        stop.assert_called_once()
        self.assertEqual(stop.call_args.args[1], {'agents'})
        self.assertEqual(commands, [['systemctl', '--user', 'disable', '--now', 'agent-waker.timer']])
        self.assertIn('GROUPS: agents\n', out)

    def test_open_is_refused_for_anyone_else(self):
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(['--open'], me, found={'agent-waker.timer'})
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('only for the recovery agent', err)

    def test_open_gate_decides_instead_of_the_ancestor_check(self):
        text = (HERE / 'config.example.toml').read_text().replace(
            '# gate = ["~/.local/bin/check-stop-request"]', 'gate = ["/usr/local/bin/check-stop-request"]')
        me = proc(50, 1, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, _, stop, _, _ = self.run_main(['--open'], me, config_text=text, gate=lambda g: True)
        self.assertEqual(code, 0)
        stop.assert_called_once()
        beneath = proc(50, 5, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(['--open'], beneath, config_text=text,
                                                     gate=lambda g: False, extra=self.RECOVERY)
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('gate did not pass', err)

    def test_open_never_takes_a_callers_config(self):
        me = proc(50, 5, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(['--open', '--config', '/tmp/other.toml'], me,
                                                     extra=self.RECOVERY)
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('--config is not allowed', err)
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/nonexistent', 'KA_RECOVERY_NAME': 'x'}):
            code, _, stop, _, _ = self.run_main(['--open'], me, extra=self.RECOVERY)
        self.assertEqual(code, 0)

    def test_open_without_ka_open_table_is_a_config_error(self):
        text = (HERE / 'config.example.toml').read_text().split('[ka_open]')[0]
        me = proc(50, 5, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(['--open'], me, config_text=text, extra=self.RECOVERY)
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('ka open is not configured', err)

    def test_plain_ka_is_refused_beneath_the_recovery_agent(self):
        me = proc(50, 5, 'python3', ['python3', '/bin/agents-killall'], cgroup=TERMINAL)
        code, commands, stop, _, err = self.run_main(['--yes'], me, extra=self.RECOVERY)
        self.assertEqual((code, commands), (2, []))
        stop.assert_not_called()
        self.assertIn('only run ka open', err)

    def test_check_config_inspects_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()), \
             patch.object(k, 'processes', side_effect=AssertionError('no inspection')), \
             patch.object(k, 'run', side_effect=AssertionError('no commands')):
            self.assertEqual(k.main(['--check-config', '--config', str(HERE / 'config.example.toml')]), 0)
            bad = Path(tmp) / 'c.toml'
            bad.write_text('[[group]]\nname = "x"\nnames = "claude"\n')
            self.assertEqual(k.main(['--check-config', '--config', str(bad)]), 2)
        self.assertIn('CONFIG OK', out.getvalue())

    def test_bad_config_exits_two_before_inspecting(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()) as err, \
             patch.object(k, 'processes', side_effect=AssertionError('no inspection')):
            config = Path(tmp) / 'c.toml'
            config.write_text('[[group]]\nname = "x"\n')
            self.assertEqual(k.main(['--yes', '--config', str(config)]), 2)
            self.assertEqual(k.main(['--config', str(Path(tmp) / 'missing.toml')]), 2)
        self.assertIn('config error', err.getvalue())

    def test_mode_flags_are_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            k.parser().parse_args(['--dry-run', '--yes'])


def ka_copy(tmp):
    """ka next to an inert recorder named agents-killall: the real tool is never reachable."""
    d = Path(tmp)
    shutil.copy(HERE / 'ka', d / 'ka')
    stub = d / 'agents-killall'
    # ka runs it with /usr/bin/python3 -I, so the stub is Python; it records its arguments.
    stub.write_text('import sys\nfrom pathlib import Path\nhere = Path(sys.argv[0]).parent\n'
                    "(here / 'calls').write_text(''.join(a + '\\n' for a in sys.argv[1:]))\n"
                    "(here / 'isolated').write_text(str(sys.flags.isolated))\n"
                    "print('\\n'.join(sys.argv[1:]))\n")
    return d


def run_on_pty(command, timeout=20):
    """Run with stdin/stdout on a PTY; returns once the child exits, never blocks forever."""
    master, slave = pty.openpty()
    try:
        child = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=subprocess.PIPE)
        os.close(slave)
        slave = None
        output, deadline = b'', time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                child.kill()
                child.wait()
                raise AssertionError('child did not exit in time')
            if not select.select([master], [], [], left)[0]:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:  # EIO once every slave descriptor is closed
                break
            if not chunk:
                break
            output += chunk
        code = child.wait(timeout=timeout)
        error = child.stderr.read().decode()
        child.stderr.close()
    finally:
        os.close(master)
        if slave is not None:
            os.close(slave)
    return code, output.decode(), error


class Ka(unittest.TestCase):
    def test_refuses_without_a_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ka_copy(tmp)
            r = subprocess.run(['bash', str(d / 'ka')], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=20, check=False)
            self.assertEqual(r.returncode, 2)
            self.assertIn('requires interactive', r.stderr)
            self.assertFalse((d / 'calls').exists())

    def test_terminal_forwards_yes_and_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ka_copy(tmp)
            code, out, err = run_on_pty(['bash', str(d / 'ka'), '--include', 'proxies'])
            self.assertEqual(code, 0, err)
            self.assertEqual(out.splitlines(), ['--yes', '--include', 'proxies'])
            self.assertEqual((d / 'calls').read_text().splitlines(), ['--yes', '--include', 'proxies'])
            self.assertEqual((d / 'isolated').read_text(), '1')

    def test_open_needs_no_terminal_and_forwards_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ka_copy(tmp)
            r = subprocess.run(['bash', str(d / 'ka'), 'open', '--include', 'proxies'], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=20, check=False,
                               env={**os.environ, 'KA_RECOVERY_NAME': 'x', 'AGENTS_KILLALL_CONFIG': '/tmp/x'})
            self.assertEqual(r.returncode, 0, r.stderr)
            # Nothing about the recovery agent or the config is passed from the environment.
            self.assertEqual((d / 'calls').read_text().splitlines(), ['--open', '--include', 'proxies'])
            self.assertEqual((d / 'isolated').read_text(), '1')

    def test_refusal_on_a_terminal_does_not_hang(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = ka_copy(tmp)
            (d / 'agents-killall').write_text("import sys\nprint('refused', file=sys.stderr)\nsys.exit(2)\n")
            code, out, err = run_on_pty(['bash', str(d / 'ka')], timeout=10)
            self.assertEqual((code, out), (2, ''))
            self.assertIn('refused', err)


class Install(unittest.TestCase):
    """install.sh is never run by these tests; only its text is checked."""

    def test_refuses_without_a_config_before_changing_anything(self):
        text = (HERE / 'install.sh').read_text()
        check = text.index('if [[ ! -f $config ]]')
        validate = text.index('--check-config')
        self.assertLess(check, validate)
        for change in ('mkdir', 'mv --', 'ln -s'):
            self.assertLess(validate, text.index(change), change)
        self.assertIn('exit 2', text[check:validate])
        self.assertIn('set -euo pipefail', text)


if __name__ == '__main__':
    unittest.main()
