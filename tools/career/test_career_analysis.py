#!/usr/bin/env python3
"""Checks for trace_report.py and save_layout.py, on synthetic input.

Both tools are analysers, and an analyser that has never been run is not
evidence. This builds a `career` log in exactly the format
runtime/kernel/career_trace.cpp writes (tests/career_trace_test.cpp pins that
format byte for byte) holding two mount sessions, and a save-like file with a
repeating record stride and a trailing CRC32, then asserts what the tools say
about each.

    python3 tools/career/test_career_analysis.py

Nothing here reads a device, a save of the user's, or the retail executable.
"""
import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        failures.append(name)


def run(*args):
    result = subprocess.run([PY, *args], capture_output=True, text=True)
    return result


# ---- a career log in the emitter's own format ----------------------------

MASTER_LEAGUE = [
    "career 5120341 mount path=save0: to=saves/SGJC0001 flags=0x00000006 "
    "note=existing stack=8234AB00,8200A1C4 thread=main thread",
    "career 5120390 open path=save0:\\savegame.svg to=saves/SGJC0001/savegame.svg "
    "size=142336 handle=0x0001000C note=existing, read-only stack=8234AB00 thread=main thread",
    "career 5120391 read path=save0:\\savegame.svg to=saves/SGJC0001/savegame.svg "
    "size=4096 done=4096 at=0 handle=0x0001000C stack=8234AB00 thread=guest thread 3",
    "career 5120392 read path=save0:\\savegame.svg to=saves/SGJC0001/savegame.svg "
    "size=4096 done=4096 at=4096 handle=0x0001000C stack=8234AB00 thread=guest thread 3",
    "career 5120399 close path=save0:\\savegame.svg to=saves/SGJC0001/savegame.svg "
    "size=142336 handle=0x0001000C thread=main thread",
    "career 5120450 unmount path=save0: to=saves/SGJC0001 thread=main thread",
]

BECOME_A_LEGEND = [
    "career 9000100 mount path=save0: to=saves/BLSAVE01 flags=0x00000006 "
    "note=existing stack=8234AB00 thread=main thread",
    "career 9000110 open path=save0:\\savegame.svg to=saves/BLSAVE01/savegame.svg "
    "size=65536 handle=0x0001000D note=existing, read-only stack=8234AB00 thread=main thread",
    # The title probing for a package that is not there: a finding, not a failure.
    "career 9000111 open path=save0:\\slots.txt to=saves/BLSAVE01/slots.txt "
    "handle=0x0001000E status=0xC0000034 note=read-only thread=main thread",
    "career 9000120 read path=save0:\\savegame.svg to=saves/BLSAVE01/savegame.svg "
    "size=65536 done=65536 at=0 handle=0x0001000D stack=8234AB00 thread=main thread",
    "career 9000130 unmount path=save0: to=saves/BLSAVE01 thread=main thread",
]

NOISE = [
    "k: some unrelated log line",
    "k: the title streams a disc file: open path=DATA0000.BIN size=4096",
]


def write_log(folder, name, lines):
    path = os.path.join(folder, name)
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


# ---- a save-like file: a header, 32 records of 0x40, a trailing CRC32 ----

RECORD_SIZE = 0x40
RECORDS = 32


def build_save(folder):
    header = bytearray(0x40)
    header[0:8] = b"PES17SVG"
    body = bytearray()
    for index in range(RECORDS):
        record = bytearray(b"\x01\x02" + struct.pack(">I", 1_000 + index))
        record += bytes(((index * 7 + offset) & 0xFF) for offset in range(len(record), RECORD_SIZE))
        body += record
    data = bytes(header) + bytes(body)
    data += struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)
    path = os.path.join(folder, "savegame.svg")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def main():
    with tempfile.TemporaryDirectory() as folder:
        ml = write_log(folder, "e1-master-league.log",
                       NOISE[:1] + MASTER_LEAGUE + NOISE[1:])
        bal = write_log(folder, "e1-become-a-legend.log", BECOME_A_LEGEND)
        both = write_log(folder, "both.log", MASTER_LEAGUE + BECOME_A_LEGEND)
        save = build_save(folder)

        # ---- trace_report.py: the E1 answer -----------------------------
        result = run(os.path.join(HERE, "trace_report.py"), ml)
        out = result.stdout
        check("trace_report reports the mount session",
              "mount  saves/SGJC0001" in out, out[:600])
        check("trace_report counts operations",
              "ops   close 1  open 1  read 2" in out, out[:900])
        check("trace_report sums bytes read", "read 8,192 B" in out, out[:900])
        check("trace_report prints the open with its size",
              "save0:\\savegame.svg  size=142,336" in out, out[:1200])
        check("trace_report keeps a note containing a comma and a space",
              "note=existing, read-only" in out, out[:1200])
        check("trace_report does not mistake disc I/O for a career line",
              "DATA0000" not in out, out)
        check("trace_report ends the session at the unmount",
              "unmount  5.120450s" in out, out[:1200])
        check("trace_report names the package in its rollup",
              "packages, in the order they were mounted" in out
              and "saves/SGJC0001" in out.split("packages, in the order")[1][:200], out)

        result = run(os.path.join(HERE, "trace_report.py"), bal)
        out = result.stdout
        check("trace_report flags a non-zero status and keeps the code",
              "status non-zero on 1 op(s): 0xC0000034 x1" in out, out[:1600])
        check("trace_report does not treat a failed open as a closed file",
              "size=65,536" in out, out[:1200])

        # ---- two modes, one report: the order is the identification -----
        result = run(os.path.join(HERE, "trace_report.py"), "--timeline", both)
        out = result.stdout
        order = [line for line in out.splitlines() if "op(s)" in line]
        check("trace_report lists both packages in mount order",
              len(order) == 2 and "SGJC0001" in order[0] and "BLSAVE01" in order[1],
              out)

        # ---- a session with no unmount is open-ended, not small ---------
        truncated = write_log(folder, "truncated.log", MASTER_LEAGUE[:-1])
        out = run(os.path.join(HERE, "trace_report.py"), truncated).stdout
        check("trace_report marks a session with no unmount as open-ended",
              "open-ended" in out, out[:1200])

        # ---- the cap, and a log without the trace ----------------------
        capped = write_log(folder, "capped.log", MASTER_LEAGUE + [
            "career 9500000 stop size=250000 note=the cap was reached, so the rest of "
            "this run is not traced"])
        out = run(os.path.join(HERE, "trace_report.py"), capped).stdout
        check("trace_report warns that the trace hit its cap",
              "THE TRACE HIT ITS CAP" in out and "250000" in out, out[-600:])

        result = run(os.path.join(HERE, "trace_report.py"), os.path.join(folder, "both.log"))
        empty = write_log(folder, "empty.log", NOISE)
        result = run(os.path.join(HERE, "trace_report.py"), empty)
        check("trace_report says so when the trace was off",
              "no career lines" in result.stdout and result.returncode == 1,
              result.stdout + str(result.returncode))

        # ---- parse() survives values with spaces, directly -------------
        import trace_report
        event = trace_report.parse(
            "career 7 open path=save0:\\a to=saves/B/note.svg size=12 "
            "handle=0x00000001 note=existing, read-only thread=guest thread 3")
        check("parse keeps every field of a line whose values contain spaces",
              event["note"] == "existing, read-only"
              and event["thread"] == "guest thread 3"
              and event["size"] == 12 and event["to"] == "saves/B/note.svg",
              str(event))
        check("parse ignores a line that is not a career line",
              trace_report.parse("k: open path=DATA0000.BIN") is None)

        # ---- save_layout.py: structure ---------------------------------
        out = run(os.path.join(HERE, "save_layout.py"), "survey", save).stdout
        check("survey reports the file size", f"{RECORD_SIZE * RECORDS + 0x44:,} bytes" in out, out[:400])
        check("survey finds the record stride",
              f"stride {RECORD_SIZE:>5}" in out, out[out.find("record stride"):][:800])
        check("survey finds the header's own text",
              "'PES17SVG'" in out, out[out.find("ASCII runs"):][:600])
        check("survey does not claim a field meaning",
              "does not say where a record begins" in out, out[-900:])

        # ---- save_layout.py: the trailer CRC32 --------------------------
        out = run(os.path.join(HERE, "save_layout.py"), "checksums", save).stdout
        check("checksums matches the CRC32 over the file without its last word",
              "MATCH  crc32 over without the last word" in out, out)
        check("checksums says a match is only a candidate",
              "candidate, not a proof" in out, out[-400:])

        # A save whose trailer is not any of the hashes tried must say so,
        # and must not pretend that means there is no checksum.
        broken = os.path.join(folder, "no-checksum.svg")
        with open(save, "rb") as handle:
            data = bytearray(handle.read())
        data[-4:] = b"\xde\xad\xbe\xef"
        with open(broken, "wb") as handle:
            handle.write(data)
        out = run(os.path.join(HERE, "save_layout.py"), "checksums", broken).stdout
        check("checksums reports a negative result as inconclusive",
              "no match" in out and "does not prove" in out, out[-600:])

        # ---- compressed data must be described as such -----------------
        import random
        random.seed(7)
        compressed = os.path.join(folder, "compressed.svg")
        with open(compressed, "wb") as handle:
            handle.write(bytes(random.getrandbits(8) for _ in range(16384)))
        out = run(os.path.join(HERE, "save_layout.py"), "survey", compressed).stdout
        check("survey calls dense, flat data compressed or encrypted",
              "compressed or encrypted" in out, out[:900])

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
