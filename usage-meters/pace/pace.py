#!/usr/bin/python3 -B
"""MAIN-only metadata scan and guarded native assistance. Disabled by default.

Config (~/.config/code-pace/config.json, or --config):
  enabled            true to let --apply launch helpers
  projectId          T3 project to scan (required)
  environmentId      T3 environment id the activity snapshot must report (required)
  excludedThreadIds  thread ids never to assist
  holdFiles          files holding an ISO time; while it is in the future, nothing launches.
                     Relative paths are under $HOME. Missing files mean no hold.
  admissionCommand   command whose JSON says whether usage allows a helper
                     (default ~/.local/bin/usage-helper-admission, from claude-usage)
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

STATUSES = {'idle', 'preparing', 'queued', 'starting', 'running', 'waiting',
            'completed', 'interrupted', 'failed', 'cancelled', 'rolled_back'}
REASONS = {None, 'archived', 'settled', 'excluded', 'automation_helper', 'not_running',
           'pending_request', 'thread_wait', 'stopping', 'quota', 'unknown_continuity', 'under_threshold'}


class Denied(Exception):
    pass


def timestamp(value):
    if not isinstance(value, str):
        raise Denied('unknown_timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError('UTC required')
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise Denied('invalid_timestamp') from None


def fresh(value, now):
    age = now - timestamp(value)
    if not 0 <= age <= 15:
        raise Denied('stale_or_future_snapshot')


def inspect_snapshot(data, now, exclusions, project, environment):
    if not project or not environment:
        raise Denied('wrong_scope')
    if not isinstance(data, dict) or data.get('environmentId') != environment or data.get('projectId') != project:
        raise Denied('wrong_scope')
    fresh(data.get('observedAt'), now)
    rows = data.get('threads')
    if not isinstance(rows, list):
        raise Denied('unknown_threads')
    candidates, seen, working = [], set(), 0
    for row in rows:
        if not isinstance(row, dict):
            raise Denied('invalid_thread_metadata')
        thread = row.get('threadId')
        if not isinstance(thread, str) or not thread or thread in seen:
            raise Denied('invalid_thread_identity')
        seen.add(thread)
        for field in ('eligible', 'automationHelper', 'settled', 'archived', 'blockedOnThreadWait'):
            if type(row.get(field)) is not bool:
                raise Denied('unknown_thread_gate')
        count = row.get('pendingRequestCount')
        if type(count) is not int or count < 0 or row.get('status') not in STATUSES or not isinstance(row.get('providerInstanceId'), str):
            raise Denied('unknown_thread_gate')
        if not row['providerInstanceId'] or 'reason' not in row or not isinstance(row['reason'], (str, type(None))) or row['reason'] not in REASONS:
            raise Denied('unknown_thread_gate')
        running = row['status'] == 'running' and not row['blockedOnThreadWait'] and count == 0
        if running and row['providerInstanceId'] == 'codex':
            working += 1
        if (thread in exclusions or not running or not row['eligible'] or row.get('reason') is not None
                or row['automationHelper'] or row['settled'] or row['archived']):
            continue
        run = row.get('activeRunId')
        if not isinstance(run, str) or not run:
            continue
        since = timestamp(row.get('continuousWorkingSince'))
        if timestamp(data['observedAt']) - since > 300:
            candidates.append(row)
    if working > 4:
        raise Denied('codex_concurrency')
    return sorted(candidates, key=lambda row: (row['continuousWorkingSince'], row['threadId']))


def admission_ok(code, data, now):
    if (code != 0 or not isinstance(data, dict) or type(data.get('schema')) is not int
            or data['schema'] != 1 or data.get('allowed') is not True
            or data.get('status') != 'clear' or data.get('reasons') != []):
        raise Denied('usage_admission_denied')
    fresh(data.get('measured_at'), now)


def atomic_json(path, data):
    fd, name = tempfile.mkstemp(prefix='.pace-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Runner:
    def __init__(self, home=None, hold_files=()):
        self.home = home or Path.home()
        self.cli = os.environ.get('T3_CLI') or self.home / '.t3/bin/t3'
        self.state = self.home / '.local/state/code-pace'
        self.hold_files = list(hold_files)

    def command(self, argv):
        try:
            result = subprocess.run([str(arg) for arg in argv], capture_output=True, text=True,
                                    timeout=20, check=False)
            return result.returncode, result.stdout
        except (OSError, subprocess.TimeoutExpired):
            raise Denied('command_unavailable') from None

    def json_command(self, argv):
        code, output = self.command(argv)
        try:
            return code, json.loads(output)
        except (ValueError, TypeError):
            raise Denied('invalid_command_json') from None

    def local_gates(self):
        try:
            values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
            available = values['MemAvailable'].split()
            if len(available) != 2 or available[1] != 'kB' or int(available[0]) < 8 * 1024 * 1024:
                raise Denied('memory_hold')
        except (OSError, KeyError, ValueError):
            raise Denied('memory_unknown') from None
        for relative in self.hold_files:
            path = self.home / Path(relative).expanduser()
            try:
                value = path.read_text().strip()
            except FileNotFoundError:
                continue  # Optional hold files: absence means no hold was published.
            except OSError:
                raise Denied('hold_unknown') from None
            if timestamp(value) > time.time():
                raise Denied('load_or_run_hold')
        code, units = self.command(['systemctl', '--user', 'list-units', '--type=service',
                                    '--state=active,activating', '--no-legend', '--plain', 't3-load-*'])
        if code != 0:
            raise Denied('loader_state_unknown')
        if units.strip():
            raise Denied('loader_active')

    def tick(self, config, apply, seen):
        if not isinstance(config, dict) or type(config.get('enabled')) is not bool:
            raise Denied('invalid_config')
        extra = config.get('excludedThreadIds', [])
        if not isinstance(extra, list) or any(not isinstance(item, str) or not item for item in extra):
            raise Denied('invalid_exclusions')
        project, environment = config.get('projectId'), config.get('environmentId')
        if not isinstance(project, str) or not isinstance(environment, str) or not project or not environment:
            raise Denied('invalid_config')
        holds = config.get('holdFiles', [])
        if not isinstance(holds, list) or any(not isinstance(item, str) or not item for item in holds):
            raise Denied('invalid_config')
        self.hold_files = holds
        admission_command = Path(config.get('admissionCommand') or self.home / '.local/bin/usage-helper-admission').expanduser()
        if apply and not config['enabled']:
            return {'status': 'disabled'}
        self.local_gates()
        code, snapshot = self.json_command([self.cli, 'thread', 'activity', '--project', project, '--json'])
        if code != 0:
            raise Denied('native_activity_unavailable')
        candidates = inspect_snapshot(snapshot, time.time(), set(extra), project, environment)
        for row in candidates:
            key = hashlib.sha256((row['threadId'] + '\0' + row['activeRunId']).encode()).hexdigest()
            if key in seen:
                continue
            # Re-read local and usage gates before EVERY assist, including retries.
            self.local_gates()
            code, admission = self.json_command([admission_command])
            admission_ok(code, admission, time.time())
            inspect_snapshot(snapshot, time.time(), set(extra), project, environment)
            if not apply:
                return {'status': 'ready_not_launched', 'threadId': row['threadId']}
            code, result = self.json_command([self.cli, 'thread', 'assist', row['threadId'],
                '--run', row['activeRunId'], '--project', project, '--origin', 'pace-v1',
                '--provider', 'codex', '--model', 'gpt-6.1-sol', '--option', 'reasoningEffort=low', '--json'])
            if (code != 0 or not isinstance(result, dict) or type(result.get('replayed')) is not bool
                    or any(not isinstance(result.get(field), str) or not result[field]
                           for field in ('taskId', 'childThreadId', 'childRunId'))):
                raise Denied('native_assist_unconfirmed')
            receipt = {field: result[field] for field in ('taskId', 'childThreadId', 'childRunId', 'replayed')}
            seen[key] = receipt
            atomic_json(self.state / 'receipts.json', seen)
            return {'status': 'replayed' if receipt['replayed'] else 'launched', **receipt}
        return {'status': 'no_eligible_target'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Also requires enabled=true in config')
    parser.add_argument('--config', type=Path, default=Path.home() / '.config/code-pace/config.json')
    args = parser.parse_args()
    runner = Runner()
    try:
        config = json.loads(args.config.read_text())
        runner.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(runner.state / 'scan.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Denied('scan_already_running') from None
            try:
                seen = json.loads((runner.state / 'receipts.json').read_text())
            except FileNotFoundError:
                seen = {}
            if not isinstance(seen, dict):
                raise Denied('invalid_receipts')
            result = runner.tick(config, args.apply, seen)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Denied, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, Denied) else 'configuration_or_state_unavailable'
        print(json.dumps({'status': 'denied', 'reason': reason}))
        return 1


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
