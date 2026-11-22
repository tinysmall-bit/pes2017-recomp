#!/usr/bin/env python3
"""Look inside the save packages the runtime writes, without the game running.

A save is a directory under the runtime's saves/ (runtime/kernel/content.cpp):
the title mounts it as a drive ("save0:") and writes its own file -- savegame.svg
-- into it through the ordinary file API, so what is on disk is exactly the bytes
PES wrote. Nothing in this tool needs default.xex, the recompiler or a device: it
reads copies of that folder.

The point is to establish a field by its cause rather than by guessing an
offset. Copy one career, make exactly one change in the game, save normally with
PES's own save, and diff the two copies: every byte that moved is attributable to
that change. `find` then locates a value you already know (a budget, a rating)
once you have an idea of its width and byte order.

    tools/career/save_probe.py list <saves-dir>
    tools/career/save_probe.py info <file>
    tools/career/save_probe.py diff <a> <b> [--join N] [--context N]
    tools/career/save_probe.py find <file> <value> [--width W] [--range A:B] [--limit N]

Widths: u8 u16 u32 u64 i16 i32 f32 f64, each tried in both byte orders unless
--order be|le pins one. Offsets are always from the start of the file.

Nothing here writes. Nothing here decides what a byte means: it reports what it
sees so that a device experiment can confirm it.
"""
import argparse
import hashlib
import os
import struct
import sys

SAVE_NAME = "savegame.svg"      # what the title writes into a package (content.cpp)


# ---- reading -------------------------------------------------------------

def read(path):
    with open(path, "rb") as f:
        return f.read()


def guest_files(root):
    """(package, path) for every file under a saves/ folder, .bak packages marked."""
    out = []
    for entry in sorted(os.listdir(root)):
        package = os.path.join(root, entry)
        if not os.path.isdir(package):
            continue
        for base, _dirs, files in os.walk(package):
            for name in sorted(files):
                out.append((entry, os.path.join(base, name)))
    return out


def hexdump(data, offset, width=16):
    for start in range(0, len(data), width):
        chunk = data[start:start + width]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        cells = " ".join(f"{b:02x}" for b in chunk)
        print(f"  {offset + start:08x}  {cells:<{width * 3}}  {text}")


def scalars(data, offset):
    """The same 8 bytes read every way an editor would want them, aligned to offset."""
    if offset + 8 > len(data):
        return ""
    word = data[offset:offset + 8]
    parts = [f"u8={word[0]}"]
    for fmt, name in ((">H", "be16"), ("<H", "le16"), (">I", "be32"), ("<I", "le32"),
                      (">f", "bef32"), ("<f", "lef32"), (">Q", "be64"), ("<Q", "le64")):
        value = struct.unpack(fmt, word[:struct.calcsize(fmt)])[0]
        parts.append(f"{name}={value:.6g}" if isinstance(value, float) else f"{name}={value}")
    return " ".join(parts)


# ---- commands ------------------------------------------------------------

def cmd_list(args):
    root = args.saves
    if not os.path.isdir(root):
        raise SystemExit(f"not a folder: {root}")
    entries = guest_files(root)
    if not entries:
        print(f"no save packages under {root}")
        return
    packages = {}
    for package, path in entries:
        packages.setdefault(package, []).append(path)
    print(f"{len(packages)} package(s) under {root}\n")
    for package in sorted(packages):
        files = packages[package]
        backup = " (rotated-aside previous save)" if package.endswith(".bak") else ""
        print(f"  {package}{backup}")
        for path in files:
            size = os.path.getsize(path)
            name = os.path.relpath(path, os.path.join(root, package))
            mark = "  <- the title's save" if os.path.basename(name) == SAVE_NAME else ""
            print(f"      {size:>12,}  {name}{mark}")
        print()


def cmd_info(args):
    data = read(args.file)
    digest = hashlib.sha256(data).hexdigest()
    print(f"{args.file}")
    print(f"  size    {len(data):,} bytes")
    print(f"  sha256  {digest}")
    if not data:
        return
    print("  head:")
    hexdump(data[:64], 0)
    print(f"  first words  {scalars(data, 0)}")
    # Where the file stops repeating: a header is usually followed by records.
    tail = data[-32:] if len(data) > 32 else b""
    if tail:
        print("  tail:")
        hexdump(tail, len(data) - len(tail))


def differ(a, b, join):
    """[(offset, length)] of differing bytes, runs joined across small gaps."""
    limit = min(len(a), len(b))
    runs = []
    i = 0
    while i < limit:
        if a[i] == b[i]:
            i += 1
            continue
        start, last = i, i
        while i < limit:
            if a[i] != b[i]:
                last = i
                i += 1
                continue
            if i - last > join:
                break
            i += 1
        runs.append((start, last - start + 1))
    return runs


def cmd_diff(args):
    a, b = read(args.left), read(args.right)
    print(f"A  {args.left}  {len(a):,} bytes")
    print(f"B  {args.right}  {len(b):,} bytes")
    if len(a) != len(b):
        print(f"  sizes differ by {len(b) - len(a):+,} byte(s); comparing the common "
              f"{min(len(a), len(b)):,} and reporting the tail separately")
    runs = differ(a, b, args.join)
    total = sum(length for _offset, length in runs)
    print(f"\n{total} differing byte(s) in {len(runs)} run(s)\n")
    for offset, length in runs:
        print(f"  {offset:#010x}  {length} byte(s)")
        print(f"    A  {a[offset:offset + length].hex()}")
        print(f"    B  {b[offset:offset + length].hex()}")
        print(f"    as  {scalars(a, offset)}")
        print(f"        {scalars(b, offset)}")
        # A field is usually wider than the bytes that moved: 500,000 -> 999,999
        # changes three bytes inside one 32-bit word, because both share a top
        # byte of zero. Report the word the run sits in, both ways round.
        word = offset & ~3
        if word + 4 <= len(a):
            left, right = a[word:word + 4], b[word:word + 4]
            print(f"    word @{word:#010x}  A {int.from_bytes(left, 'big')} (BE)"
                  f" / {int.from_bytes(left, 'little')} (LE)    "
                  f"B {int.from_bytes(right, 'big')} (BE)"
                  f" / {int.from_bytes(right, 'little')} (LE)")
        if length in (1, 2, 4, 8):
            print(f"    int A={int.from_bytes(a[offset:offset + length], 'big')} (BE)"
                  f" / {int.from_bytes(a[offset:offset + length], 'little')} (LE)"
                  f"   B={int.from_bytes(b[offset:offset + length], 'big')} (BE)"
                  f" / {int.from_bytes(b[offset:offset + length], 'little')} (LE)")
        print()
    if len(a) != len(b):
        longer, name = (a, "A") if len(a) > len(b) else (b, "B")
        extra = longer[min(len(a), len(b)):]
        print(f"  {name} has {len(extra):,} byte(s) past the common length:")
        hexdump(extra[:64], min(len(a), len(b)))


WIDTHS = {
    "u8": (1, False), "u16": (2, False), "u32": (4, False), "u64": (8, False),
    "i16": (2, True), "i32": (4, True), "f32": (4, None), "f64": (8, None),
}


def occurrences(data, needle):
    out = []
    at = data.find(needle)
    while at >= 0:
        out.append(at)
        at = data.find(needle, at + 1)
    return out


def cmd_find(args):
    data = read(args.file)
    begin, end = 0, len(data)
    if args.range:
        lo, _, hi = args.range.partition(":")
        begin = int(lo, 0) if lo else 0
        end = int(hi, 0) if hi else len(data)
        if not (0 <= begin < end <= len(data)):
            raise SystemExit(f"--range {args.range} is outside a {len(data):,}-byte file")
    window = data[begin:end]

    names = [args.width] if args.width else list(WIDTHS)
    orders = [args.order] if args.order else ["be", "le"]
    found = 0
    for name in names:
        size, signed = WIDTHS[name]
        try:
            value = float(args.value) if name.startswith("f") else int(args.value, 0)
        except (ValueError, OverflowError) as error:
            raise SystemExit(f"{args.value!r} is not a {name}: {error}")
        if not name.startswith("f") and not signed and value < 0:
            continue
        for short in orders:
            byteorder = "big" if short == "be" else "little"
            try:
                if name.startswith("f"):
                    needle = struct.pack((">" if short == "be" else "<") +
                                         ("f" if size == 4 else "d"), value)
                else:
                    # A value too large for a width is not a match, not an error.
                    needle = value.to_bytes(size, byteorder, signed=bool(signed))
            except (OverflowError, struct.error):
                continue
            for at in occurrences(window, needle):
                absolute = begin + at
                print(f"  {name} {short}  {absolute:#010x}   {scalars(data, absolute)}")
                found += 1
                if found >= args.limit:
                    print(f"  ... stopped at --limit {args.limit}")
                    return
    if not found:
        print(f"no {args.value} in {args.file} at {args.range or 'any offset'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="the packages and files under a saves/ folder")
    p.add_argument("saves")
    p.set_defaults(run=cmd_list)

    p = sub.add_parser("info", help="size, hash, head and tail of one file")
    p.add_argument("file")
    p.set_defaults(run=cmd_info)

    p = sub.add_parser("diff", help="every byte two saves disagree on")
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("--join", type=int, default=3,
                   help="bridge runs separated by at most N equal bytes (default 3)")
    p.add_argument("--context", type=int, default=0,
                   help="bytes of context to show around each run (default 0)")
    p.set_defaults(run=cmd_diff)

    p = sub.add_parser("find", help="locate a value you can read in the game")
    p.add_argument("file")
    p.add_argument("value")
    p.add_argument("--width", choices=sorted(WIDTHS), help="otherwise every width is tried")
    p.add_argument("--order", choices=["be", "le"], help="otherwise both are tried")
    p.add_argument("--range", help="A:B, offset range to search")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(run=cmd_find)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
