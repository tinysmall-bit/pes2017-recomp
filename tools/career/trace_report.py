#!/usr/bin/env python3
"""Turn a PES17_TRACE_CAREER log into the answer experiment E1 asks for.

E1 is "which mounted package belongs to Master League, and which to Become a
Legend?" -- and the trace answers it only if you read it in order. This reads the
`career` lines out of a log, groups them into the sessions between a `mount` and
its `unmount`, and prints, per session: the package, the drive, the flags, how
many operations of each kind, how many bytes were read and written, every open
with its size and note, the guest stacks that made the calls, and any rename
(a save being published).

    tools/career/trace_report.py reports/career/e1-master-league.log
    tools/career/trace_report.py --timeline reports/career/e1-*.log
    adb logcat -d | tools/career/trace_report.py -

Two things it is careful about, because both mislead otherwise:

- A session that never saw its `unmount` -- the title was killed, or the log was
  cut -- is reported as open-ended rather than as a smaller session.
- A non-zero `status` is counted and shown. The title probes for packages and
  files that are not there, and a trace full of `open ... status=0xC0000034`
  means "it looked here and found nothing", which is a finding, not a failure.

Nothing here reads or writes a save. It only reads a log.
"""
import argparse
import re
import sys
from collections import Counter

# career_trace::Format (runtime/kernel/career_trace.cpp) writes these keys, in
# this order. Values may contain spaces -- "note=existing, read-only",
# "thread=guest thread 3" -- so the split is on " <key>=" and not on "=".
KEYS = ("path", "to", "size", "done", "at", "handle", "status",
        "flags", "note", "stack", "thread")
FIELD = re.compile(r"\s+(?=(?:%s)=)" % "|".join(KEYS))
HEAD = re.compile(r"^career\s+(\d+)\s+([a-z]+)\s*(.*)$")
NUMBERED = ("size", "done", "at")


def parse(line):
    """One log line as a dict, or None when it is not a career line."""
    match = HEAD.match(line)
    if not match:
        return None
    event = {"us": int(match.group(1)), "op": match.group(2)}
    rest = match.group(3).strip()
    if rest:
        for part in FIELD.split(rest):
            key, _, value = part.partition("=")
            if key in KEYS:
                event[key] = value
    for key in NUMBERED:
        if key in event:
            try:
                event[key] = int(event[key])
            except ValueError:
                pass
    return event


def parse_lines(lines):
    return [event for event in (parse(line.rstrip("\n")) for line in lines) if event]


class Session:
    """Everything between one mount and its unmount."""

    def __init__(self, mount):
        self.mount = mount
        self.unmount = None
        self.ops = Counter()
        self.read_bytes = 0
        self.write_bytes = 0
        self.opens = []
        self.renames = []
        self.stacks = Counter()
        self.statuses = []
        self.handles = set()

    def add(self, event):
        self.ops[event["op"]] += 1
        if event["op"] == "open":
            self.opens.append(event)
        if event["op"] == "rename":
            self.renames.append(event)
        if event["op"] in ("read", "write") and isinstance(event.get("done"), int):
            if event["op"] == "read":
                self.read_bytes += event["done"]
            else:
                self.write_bytes += event["done"]
        if event.get("stack"):
            self.stacks[event["stack"]] += 1
        if event.get("handle"):
            self.handles.add(event["handle"])
        if event.get("status"):
            self.statuses.append(event)

    @property
    def package(self):
        return self.mount.get("to", "") or "(unnamed)"

    @property
    def drive(self):
        return self.mount.get("path", "")

    @property
    def seconds(self):
        return self.mount["us"] / 1e6


def sessions(events):
    """([Session], [Event]) -- the events outside any session come second."""
    found, current, outside = [], None, []
    for event in events:
        op = event["op"]
        if op == "mount":
            current = Session(event)
            found.append(current)
            continue
        if op == "unmount":
            if current is not None:
                current.unmount = event
                current = None
            else:
                outside.append(event)
            continue
        if current is None:
            outside.append(event)
            continue
        current.add(event)
    return found, outside


def note_of(event):
    return event.get("note", "")


def render_session(index, session):
    mount = session.mount
    line = f"  #{index}  {session.seconds:>12.6f}s  mount  {session.package}"
    if session.drive:
        line += f"  (drive {session.drive})"
    print(line)
    details = []
    if mount.get("flags"):
        details.append(f"flags={mount['flags']}")
    if mount.get("note"):
        details.append(f"note={mount['note']}")
    if mount.get("status"):
        details.append(f"status={mount['status']}")
    if details:
        print("        " + "  ".join(details))

    if session.ops:
        counts = "  ".join(f"{op} {count}" for op, count in sorted(session.ops.items()))
        print(f"        ops   {counts}")
    moved = f"read {session.read_bytes:,} B"
    if session.ops.get("write"):
        moved += f", wrote {session.write_bytes:,} B"
    if session.handles:
        moved += f", {len(session.handles)} handle(s)"
    print(f"        moved {moved}")

    for opened in session.opens:
        path = opened.get("path", "?")
        extra = []
        if "size" in opened:
            extra.append(f"size={opened['size']:,}")
        if opened.get("note"):
            extra.append(f"note={opened['note']}")
        if opened.get("status"):
            extra.append(f"status={opened['status']}")
        print(f"        open  {path}  " + "  ".join(extra))

    for renamed in session.renames:
        source, target = renamed.get("path", "?"), renamed.get("to", "?")
        print(f"        rename {source}  ->  {target}")
        print("               (a save is published by this rename)")

    for stack, count in session.stacks.most_common(3):
        print(f"        stack {stack}   ({count} of {sum(session.ops.values())} events)")

    if session.statuses:
        codes = Counter(event.get("status", "") for event in session.statuses)
        listed = ", ".join(f"{code} x{count}" for code, count in codes.most_common())
        print(f"        status non-zero on {len(session.statuses)} op(s): {listed}")

    if session.unmount is None:
        print("        unmount  none -- this session is open-ended (killed, or the log ends)")
    else:
        print(f"        unmount  {session.unmount['us'] / 1e6:.6f}s")


def render(events, title):
    found, outside = sessions(events)
    print(f"== {title}  ({len(events)} career line(s), {len(found)} mount session(s))")
    if not events:
        print("   no career lines: the trace was off for this run, or the log is the wrong file")
        return 1

    for index, session in enumerate(found, 1):
        render_session(index, session)
        print()

    if not found:
        print("  no mount at all. The title did not open a content package: either the run")
        print("  never reached a career screen, or this build did not create the package.")
        print()

    # The order alone answers "which package is which": one mode per capture.
    order = {}
    for session in found:
        entry = order.setdefault(session.package, {"n": 0, "read": 0, "write": 0,
                                                   "first": session.seconds})
        entry["n"] += 1
        entry["read"] += session.read_bytes
        entry["write"] += session.write_bytes
    if order:
        print("  packages, in the order they were mounted")
        for package, entry in order.items():
            print(f"    {package:<28} {entry['n']} session(s)  "
                  f"read {entry['read']:,} B  wrote {entry['write']:,} B  "
                  f"first at {entry['first']:.6f}s")
        print()

    published = [event for session in found for event in session.renames]
    if published:
        print("  published (a rename, which is the moment a save becomes real)")
        for event in published:
            print(f"    {event['us'] / 1e6:>12.6f}s  {event.get('path', '?')} -> "
                  f"{event.get('to', '?')}")
        print()

    if outside:
        print("  outside any mount (a package rotated or deleted while not mounted)")
        for event in outside:
            print(f"    {event['us'] / 1e6:>12.6f}s  {event['op']}  "
                  f"{event.get('path', '')} {event.get('to', '')}".rstrip())
        print()

    stopped = [event for event in events if event["op"] == "stop"]
    for event in stopped:
        print(f"  THE TRACE HIT ITS CAP at {event['us'] / 1e6:.6f}s"
              f" ({event.get('size', '?')} events): this log is not the whole run.")
    return 0


def render_timeline(events, title):
    found, _outside = sessions(events)
    print(f"== {title}")
    for session in found:
        end = "open" if session.unmount is None else f"{session.unmount['us'] / 1e6:.6f}s"
        print(f"  {session.seconds:>12.6f}s  {session.package:<28} "
              f"{sum(session.ops.values()):>5} op(s)  read {session.read_bytes:>10,} B  "
              f"wrote {session.write_bytes:>9,} B  ends {end}")
    return 0 if events else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", help="log files, or - for stdin")
    parser.add_argument("--timeline", action="store_true",
                        help="one line per session, for comparing several runs at once")
    args = parser.parse_args(argv)

    status = 0
    for path in args.logs:
        if path == "-":
            text = sys.stdin.read().splitlines()
        else:
            try:
                with open(path, "r", errors="replace") as handle:
                    text = handle.read().splitlines()
            except OSError as error:
                print(f"{path}: {error}", file=sys.stderr)
                status = 2
                continue
        events = parse_lines(text)
        if args.timeline:
            status |= render_timeline(events, path)
        else:
            status |= render(events, path)
    return status


if __name__ == "__main__":
    sys.exit(main())
