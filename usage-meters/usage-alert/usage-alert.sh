#!/usr/bin/python3 -I
"""Passive CLIProxyAPI 8.0.54 journal/file watcher. Never opens auth files.

Environment (all optional):
  USAGE_ALERT_UNITS   systemd user units and their provider names,
                      default "cliproxyapi.service=Codex,cliproxyapi-claude.service=Claude"
  USAGE_ALERT_DIRS    each provider's CLIProxyAPI folder (holds auth/ and logs/),
                      default "Codex=~/.cli-proxy-api,Claude=~/.cli-proxy-api-claude"
  USAGE_ALERT_MIRROR  a command that also gets each all-accounts notice ("title\nbody" on
                      stdin; exit 0 means delivered), for example a chat relay. Default: none.
"""
import argparse
import html
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import sys
import time


def pairs(text):
    """'a=b,c=d' -> {'a': 'b', 'c': 'd'}."""
    out = {}
    for item in filter(None, (part.strip() for part in text.split(","))):
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise SystemExit(f"usage-alert: bad setting {item!r}; expected key=value")
        out[key.strip()] = value.strip()
    return out


PROVIDERS = pairs(os.environ.get("USAGE_ALERT_UNITS") or "cliproxyapi.service=Codex,cliproxyapi-claude.service=Claude")
DIRS = {provider: Path(os.path.expanduser(path)) for provider, path in pairs(
    os.environ.get("USAGE_ALERT_DIRS") or "Codex=~/.cli-proxy-api,Claude=~/.cli-proxy-api-claude").items()}
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,180}\.[A-Za-z]{2,20}(?![\w.-])")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def match(line, provider, now, headers=None):
    """Return only allowlisted fields; raw lines never leave this function."""
    raw = ANSI.sub("", line).strip()
    if "upstream execution failed:" not in raw.lower():
        payload = raw[5:].strip() if raw.startswith("data:") else raw
        try:
            envelope = json.loads(payload)
        except (ValueError, TypeError):
            return None
        if not isinstance(envelope, dict) or not (
            envelope.get("error") or (envelope.get("type") == "response.failed"
                                      and envelope.get("response", {}).get("error"))):
            return None
    line = ANSI.sub("", line).replace('\\"', '"')
    low = line.lower()
    warning = "upstream execution failed:" in low
    if not warning and not ("error" in low and (line.lstrip().startswith(("{", "data:")))):
        return None
    p = re.search(r"\bprovider=(codex|claude)\b", low)
    provider = p.group(1).title() if p else provider
    if provider not in ("Codex", "Claude"):
        return None
    auth = re.search(r"\bauth_file=([^\s]+)", line) if warning else None
    account = auth.group(1).strip('"') if auth else None
    email_name = re.sub(r"^(?:codex|claude)-", "", account or "")
    email_name = re.sub(r"^[0-9a-f]{8,64}-", "", email_name)
    email_name = email_name.removesuffix(".json")
    if provider == "Codex":
        email_name = re.sub(r"-(?:edu-plus|prolite|plus|pro|free|team|business|enterprise|edu|go)$", "", email_name)
    email = EMAIL.fullmatch(email_name)
    # Never display arbitrary filenames, messages, headers, model names, keys or IDs.
    email = email.group(0) if email else ""
    headers = headers or {}
    shared_rejection = any(headers.get(k) == "rejected" for k in (
        "anthropic-ratelimit-unified-5h-status", "anthropic-ratelimit-unified-7d-status"))
    if headers.get("anthropic-ratelimit-unified-status") == "rejected":
        # Missing/overage-only shared-window evidence must remain UNKNOWN.
        overage = any("overage" in k or "7d_oi" in k or "fable" in k
                      for k, v in headers.items() if v)
        shared_rejection = shared_rejection or not overage
    cap = "usage_limit_reached" in low or "insufficient_quota" in low
    cap = cap or bool(re.search(r"(?:usage|quota|credit|weekly|daily|subscription|5.hour|7.day)[ _-]*(?:limit|cap)?[^\n]{0,45}(?:reached|exceeded|exhausted|depleted)", low))
    cap = cap or (provider == "Claude" and shared_rejection)
    cooldown = "model_cooldown" in low or "all credentials for model" in low
    rate = bool(re.search(r"\b429\b|rate_limit_error|rate_limit_exceeded|rate limit", low))
    if not (cap or cooldown or rate):
        return None
    kind = "quota" if cap else "cooldown" if cooldown else "rate-limit"
    expiry = None
    detail = ""
    for key in ("resets_at", "resets_in_seconds", "reset_seconds"):
        value = re.search(r'"' + key + r'"\s*:\s*(\d{1,12})\b', line)
        if value:
            seconds = int(value.group(1))
            expiry = seconds if key == "resets_at" else now + seconds
            detail = f" {key}={seconds}."
            break
    if expiry is None:
        for key in ("anthropic-ratelimit-unified-5h-reset", "anthropic-ratelimit-unified-7d-reset", "anthropic-ratelimit-unified-reset"):
            status = key.replace("-reset", "-status")
            value = headers.get(key, "")
            if headers.get(status) == "rejected" and re.fullmatch(r"\d{9,12}", value):
                expiry = int(value)
                detail = f" Reset Unix time: {expiry}."
                break
    if not detail and re.fullmatch(r"\d{1,8}", headers.get("retry-after", "")):
        seconds = int(headers["retry-after"])
        expiry = now + seconds
        detail = f" Retry after {seconds} seconds."
    return provider, account, email, kind, expiry, detail


class Watcher:
    def __init__(self, send, inventory=None, clock=time.time):
        self.send, self.inventory, self.clock = send, inventory or {}, clock
        self.states = {}
        self.sent = {}

    def recover(self, provider, account=None):
        # Unattributed success cannot identify who recovered: invalidate ALL proof.
        self.states = {k: v for k, v in self.states.items()
                       if not (k[0] == provider and (account is None or k[1] == account))}
        self.sent = {k: v for k, v in self.sent.items()
                     if not (k[0] == provider and (k[1] == "ALL" or (account is not None and k[1] == account)))}

    def expire(self):
        now = self.clock()
        for (provider, account), (expiry, observed) in list(self.states.items()):
            if expiry <= now or now - observed >= 600 or account not in self.inventory.get(provider, set()):
                self.recover(provider, account)
        self.sent = {k: t for k, t in self.sent.items() if now - t < 600}

    def emit(self, provider, account, kind, urgency, title, body):
        key = provider, account or "UNKNOWN", kind
        if key in self.sent:
            return
        if self.send(urgency, title, body):
            self.sent[key] = self.clock()

    def line(self, line, provider, headers=None):
        self.expire()
        event = match(line, provider, self.clock(), headers)
        if event is None:
            return
        provider, account, email, kind, expiry, detail = event
        cap = kind == "quota"
        title = ("OUT OF USAGE: " if cap else "Usage cooldown: ") + provider
        if email:
            title += " " + email
        body = {"quota": "Upstream usage/quota cap reached.", "rate-limit": "Transient upstream rate limit; usage exhaustion is unconfirmed.",
                "cooldown": "All credentials for the requested model are cooling down; provider-wide exhaustion is unconfirmed."}[kind]
        self.emit(provider, account, kind, "critical" if cap else "normal", title, body + detail)
        if account not in self.inventory.get(provider, set()):
            # An unattributed/unknown failure supplies no complete account proof.
            for key in list(self.states):
                if key[0] == provider:
                    self.states.pop(key, None)
        if account in self.inventory.get(provider, set()):
            if cap and expiry is not None and expiry > self.clock():
                self.states[provider, account] = expiry, self.clock()
            else:
                self.recover_state(provider, account)
        self.expire()
        names = self.inventory.get(provider, set())
        if names and all((provider, name) in self.states for name in names):
            reset = min(self.states[provider, name][0] for name in names)
            self.emit(provider, "ALL", "all-out", "critical", f"OUT OF USAGE: {provider} — ALL ACCOUNTS",
                      f"Every inventoried account has fresh quota-cap evidence. Earliest reset Unix time: {int(reset)}.")

    def recover_state(self, provider, account):
        self.states.pop((provider, account), None)
        self.sent.pop((provider, "ALL", "all-out"), None)


class LogFiles:
    def __init__(self, roots, watcher):
        self.roots, self.watcher, self.files = roots, watcher, {}
        self.scan(initial=True)

    def scan(self, initial=False):
        live = set()
        for provider, root in self.roots.items():
            try:
                paths = list(root.glob("*.log"))
            except OSError:
                continue
            for path in paths:
                try:
                    stat = path.stat()
                    key = provider, stat.st_dev, stat.st_ino
                    live.add(key)
                    if key not in self.files:
                        # Existing files are historical; new files are consumed from byte 0.
                        self.files[key] = [stat.st_size if initial else 0, "", {}]
                    state = self.files[key]
                    if stat.st_size < state[0]:
                        state[:] = [0, "", {}]
                    with path.open("rb") as source:
                        source.seek(state[0])
                        for _ in range(2000):
                            raw = source.readline(262144)
                            if not raw or not raw.endswith(b"\n"):
                                break
                            state[0] = source.tell()
                            line = raw.decode("utf-8", "replace").rstrip()
                            if path.name.startswith("main"):
                                self.watcher.line(line, provider)
                                continue
                            if line.startswith("=== "):
                                state[1] = line
                                state[2] = {}
                            # Never match request bodies or request headers.
                            if "RESPONSE" not in state[1]:
                                continue
                            if state[1] == "=== RESPONSE ===" and re.fullmatch(r"Status: 2\d\d", line):
                                self.watcher.recover(provider)
                            header = re.fullmatch(r"(Anthropic-Ratelimit-Unified-[A-Za-z0-9_-]+|Retry-After):\s*(\w+)", line, re.I)
                            if header:
                                state[2][header.group(1).lower()] = header.group(2).lower()
                            self.watcher.line(line, provider, state[2])
                except (OSError, ValueError):
                    continue
        self.files = {k: v for k, v in self.files.items() if k in live}


def inventory(roots):
    result = {}
    for provider, root in roots.items():
        try:
            # Include all JSON filenames conservatively, even unrecognized/disabled ones.
            result[provider] = {p.name for p in root.iterdir() if p.name.endswith(".json")}
        except OSError:
            result[provider] = set()
    return result


def category_notice(title):
    """Individual account/cooldown events are never human notifications; only all-accounts ones are."""
    return any(title == f'OUT OF USAGE: {provider} — ALL ACCOUNTS' for provider in DIRS)


def mirror_notice(title, body, run=None, command=None):
    """Copy only sanitized all-accounts notices to the optional USAGE_ALERT_MIRROR command."""
    command = os.environ.get("USAGE_ALERT_MIRROR", "") if command is None else command
    if not category_notice(title) or not command.strip():
        return False
    run = subprocess.run if run is None else run
    try:
        result = run(shlex.split(command), input=title + '\n' + body, text=True,
                     capture_output=True, timeout=3, check=False)
        return result.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="stdin matcher; safe JSON output, no desktop notification")
    parser.add_argument("--notify-test", action="store_true", help="send exactly one desktop test notification through matcher")
    parser.add_argument("--provider", choices=sorted(DIRS), default="Codex" if "Codex" in DIRS else sorted(DIRS)[0])
    args = parser.parse_args()
    if args.notify_test and not args.test:
        parser.error("--notify-test requires --test")
    test_sent = False

    def send(urgency, title, body):
        nonlocal test_sent
        if not args.test and not category_notice(title):
            return True  # Keep passive all-account evidence, suppress this notice.
        if args.test and not args.notify_test:
            print(json.dumps({"urgency": urgency, "title": title, "body": body}), flush=True)
            return True
        if args.notify_test:
            if test_sent:
                return True
            title, body = "Usage alert test (ignore)", "Synthetic usage-cap fixture matched. No API calls were made."
        matrix_ok = False if args.test else mirror_notice(title, body)
        try:
            result = subprocess.run(["notify-send", "-u", urgency, "-a", "Usage", "--", html.escape(title), html.escape(body)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode:
                print("usage-alert: desktop notification failed", file=sys.stderr, flush=True)
                return matrix_ok
            test_sent = True
            print("usage-alert: notification delivered", flush=True)
            return True
        except (OSError, subprocess.TimeoutExpired):
            print("usage-alert: desktop notification unavailable", file=sys.stderr, flush=True)
            return matrix_ok

    watcher = Watcher(send)
    if args.test:
        for line in sys.stdin:
            watcher.line(line, args.provider)
        return
    bases = DIRS
    auth_roots = {p: root / "auth" for p, root in bases.items()}
    watcher.inventory = inventory(auth_roots)
    # CLIProxyAPI writes logs next to its auth dir in some installs (auth/logs) and in <root>/logs in others.
    files = LogFiles({p: (root / "auth" / "logs" if (root / "auth" / "logs").is_dir() else root / "logs") for p, root in bases.items()}, watcher)
    units = [arg for unit in PROVIDERS for arg in ("-u", unit)]
    journal = subprocess.Popen(["journalctl", "--user", "-f", "-n", "0", *units, "-o", "json"], stdout=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    selector.register(journal.stdout, selectors.EVENT_READ)
    try:
        while True:
            for key, _ in selector.select(timeout=1):
                raw = os.read(key.fileobj.fileno(), 262144)
                if not raw:
                    raise RuntimeError("journal follower stopped")
                pending.extend(raw)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending[:] = rest
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    provider = PROVIDERS.get(record.get("_SYSTEMD_USER_UNIT"))
                    message = record.get("MESSAGE", "")
                    if provider and isinstance(message, str):
                        if re.search(r"\b2\d\d\s*\|", ANSI.sub("", message)):
                            watcher.recover(provider)
                        watcher.line(message, provider)
            watcher.inventory = inventory(auth_roots)
            watcher.expire()
            files.scan()
    finally:
        journal.terminate()
        journal.wait(timeout=5)


if __name__ == "__main__":
    pending = bytearray()
    main()
