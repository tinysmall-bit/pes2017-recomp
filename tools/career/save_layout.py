#!/usr/bin/env python3
"""Survey a save's structure and look for an integrity value, before touching it.

`save_probe.py` answers "what changed"; this answers the two questions that have
to be settled before anything may be written:

1. Is the file even plain? A save that is deflate-compressed or encrypted cannot
   be edited field by field at all -- the editor would have to hold the title's
   structure live, and a byte patch would be meaningless. `survey` measures
   entropy per block, counts surviving ASCII runs, and looks for a repeating
   record stride, which is what a table of players or fixtures looks like from
   the outside.

2. Is there a checksum, and where? `checksums` takes the plausible places for one
   (the last word, the last two words, the first word of a header) and asks
   whether any of them equals a hash of some other range of the same file.

    tools/career/save_layout.py survey   savegame.svg
    tools/career/save_layout.py survey   savegame.svg --block 4096 --max-blocks 32
    tools/career/save_layout.py checksums savegame.svg

Both are read-only, and neither decides anything. `checksums` printing "no match"
is not proof that the save has no checksum: it means none of the four hashes over
the ranges tried matches the words tried. It lists everything it tried so that
the negative result can be extended rather than assumed away.

A save is a directory holding savegame.svg (runtime/kernel/content.cpp); pass the
file, not the directory.
"""
import argparse
import hashlib
import math
import os
import re
import struct
import sys
import zlib
from collections import Counter

ASCII_RUN = re.compile(rb"[\x20-\x7e]{6,}")

# Hash names, most plausible first for a console save: a CRC is what a title
# usually writes, an additive sum is what it writes when it is being cheap.
HASHES = (
    ("crc32", lambda data: zlib.crc32(data) & 0xFFFFFFFF),
    ("adler32", lambda data: zlib.adler32(data) & 0xFFFFFFFF),
    ("sum8", lambda data: sum(data) & 0xFFFFFFFF),
    ("xor32", None),        # filled in below; needs the word view
)

# Where a checksum plausibly sits: the trailer, and the dwords of a header.
def candidate_positions(size):
    out = [size - 4, size - 8, 0, 4, 8, 0x0C, 0x10, 0x20, 0x40]
    return sorted({position for position in out if 0 <= position <= size - 4})


def xor32(data):
    usable = len(data) - len(data) % 4
    value = 0
    for (word,) in struct.iter_unpack(">I", data[:usable]):
        value ^= word
    return value & 0xFFFFFFFF


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


def entropy(chunk):
    if not chunk:
        return 0.0
    total = len(chunk)
    counts = Counter(chunk)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def bar(bits, width=24):
    """Entropy as a bar: 8 bits/byte fills it."""
    filled = max(0, min(width, round(bits / 8 * width)))
    return "#" * filled + "-" * (width - filled)


# ---- survey --------------------------------------------------------------

def stride_scores(data, low, high, step, samples):
    """How often a 2-byte pair repeats at each stride, over non-zero samples.

    A record array shows up here even when every record's values differ: what
    repeats is the *shape* -- a constant ID field, a flag, a zero gap at the same
    place in each record. The absolute agreement is small (one constant word in a
    64-byte record is one position in 64), so it is only meaningful against the
    median agreement of every stride tried: that median is what chance looks like
    in this file. The caller ranks by the ratio.
    """
    size = len(data)
    high = min(high, size - 2)
    if high < low + step:
        return []
    # One window shared by every stride. Otherwise a longer stride loses the
    # positions near the end of the file, and its agreement is a fraction of a
    # smaller, different set -- which reads as a peak where there is none.
    limit = size - high - 2
    if limit <= 0:
        return []
    stride_between = max(1, limit // max(1, samples))
    positions = list(range(0, limit, stride_between))
    scores = []
    for stride in range(low, high + 1, step):
        hits = seen = 0
        for position in positions:
            first, second = data[position], data[position + 1]
            if first == 0 and second == 0:
                continue        # padding matches at every stride; it means nothing
            seen += 1
            if first == data[position + stride] and second == data[position + stride + 1]:
                hits += 1
        if seen:
            scores.append((hits / seen, stride, seen))
    return scores


def cmd_survey(args):
    data = read(args.file)
    size = len(data)
    print(f"{args.file}")
    print(f"  size      {size:,} bytes")
    print(f"  sha256    {hashlib.sha256(data).hexdigest()}")
    if not size:
        print("  empty file")
        return 0

    overall = entropy(data)
    zeros = data.count(0) / size
    distinct = len(set(data))
    print(f"  entropy   {overall:.3f} bits/byte   {bar(overall)}")
    print(f"  zeros     {zeros:.1%} of the file")
    print(f"  distinct  {distinct} of 256 byte values")

    if overall >= 7.9 and zeros < 0.05:
        verdict = ("dense and flat: consistent with compressed or encrypted data. A field cannot be "
                   "patched here by offset; the editor would have to work on the title's live structure.")
    elif zeros > 0.30 and overall < 4.5:
        verdict = ("mostly padding and small integers: consistent with a fixed-size record table.")
    else:
        verdict = ("mixed: structured regions (low entropy, plain text) beside dense ones. Survey the "
                   "blocks below before assuming any region's meaning.")
    print(f"  verdict   {verdict}")
    print()

    block = args.block
    count = (size + block - 1) // block
    shown = min(count, args.max_blocks)
    print(f"  entropy per {block:,}-byte block, {shown} of {count} block(s)")
    step = max(1, count // shown)
    for index in range(0, min(count, shown * step), step):
        start = index * block
        chunk = data[start:start + block]
        print(f"    {start:#010x}  {entropy(chunk):5.2f} b/B  {bar(entropy(chunk), 16)}  "
              f"{len(chunk):,} B")
    if count > shown:
        print(f"    ... {count - shown} more block(s), evenly spaced ones shown")
    print()

    runs = [(match.start(), match.group().decode("ascii", "replace"))
            for match in ASCII_RUN.finditer(data)]
    print(f"  ASCII runs of 6+ characters: {len(runs)}")
    for offset, text in runs[:args.max_strings]:
        shown_text = text if len(text) <= 60 else text[:57] + "..."
        print(f"    {offset:#010x}  {shown_text!r}")
    if len(runs) > args.max_strings:
        print(f"    ... {len(runs) - args.max_strings} more")
    print()

    print(f"  record stride candidates (4..{args.stride_max} step 4, 2-byte agreement, "
          f"non-zero samples only)")
    scores = stride_scores(data, 4, args.stride_max, 4, args.samples)
    if not scores:
        print("    the file is too small for any stride in that range")
        return 0
    baseline = sorted(score for score, _stride, _seen in scores)[len(scores) // 2]
    shown_scores = sorted(scores, key=lambda entry: (entry[0], -entry[1]),
                          reverse=True)[:args.top]
    for score, stride, seen in shown_scores:
        chance = (f"{score / baseline:4.1f}x chance" if baseline > 0 else "chance ~0   ")
        multiple = ""
        if any(stride % other == 0 and stride != other for _s, other, _n in shown_scores):
            multiple = "   (a multiple of a shorter stride above: the same record)"
        print(f"    stride {stride:>5}   agreement {score:5.1%}   {chance}   "
              f"over {seen:,} sample(s){multiple}")
    print(f"    chance is the median agreement over every stride tried: {baseline:.1%}")
    peak = shown_scores and shown_scores[0][0] >= 0.01 and \
        (baseline == 0 or shown_scores[0][0] / baseline >= 1.5)
    if peak:
        print("    A peak like this says a shape repeats. It does not say where a record begins,")
        print("    which offset inside it is which field, or that every record is that size.")
    else:
        print("    No stride stands out. This file may not be a plain array of fixed-size records:")
        print("    a compressed body, or records of varying size, both look like this.")
    return 0


# ---- checksums -----------------------------------------------------------

def ranges_to_try(data):
    """(description, chunk, positions to compare) for each range a checksum might cover.

    `positions` is a list, not a range to exclude, because the two shapes differ:
    hashing the file *without* its trailer is compared against the trailer, while
    hashing the file with its own checksum word zeroed is compared against that
    same word and nothing else.
    """
    size = len(data)
    candidates = candidate_positions(size)
    out = []
    for description, start, end in (("the whole file", 0, size),
                                    ("after a 4-byte magic", 4, size),
                                    ("without the last word", 0, size - 4),
                                    ("after a 0x40 header", 0x40, size),
                                    ("after a 0x40 header, without the last word", 0x40, size - 4)):
        if 0 <= start < end <= size:
            outside = [position for position in candidates if not start <= position < end]
            if outside:
                out.append((description, data[start:end], outside))
    # A checksum is often computed over the file with its own field read as zero.
    for position in candidates:
        zeroed = data[:position] + b"\0\0\0\0" + data[position + 4:]
        out.append((f"the whole file with the word at {position:#x} zeroed",
                    zeroed, [position]))
    return out


def cmd_checksums(args):
    data = read(args.file)
    size = len(data)
    print(f"{args.file}  ({size:,} bytes)")
    if size < 16:
        print("  too small to hold a header and a checksum")
        return 0

    positions = candidate_positions(size)
    print(f"  candidate word(s): " + ", ".join(f"{position:#x}" for position in positions))
    for position in positions:
        stored = data[position:position + 4]
        print(f"    {position:#010x}  {stored.hex()}  "
              f"be={int.from_bytes(stored, 'big'):>10}  le={int.from_bytes(stored, 'little'):>10}")
    print()

    ranges = ranges_to_try(data)
    print(f"  trying {len(HASHES)} hash(es) over {len(ranges)} range(s) "
          f"against {len(positions)} word(s)")
    matches = 0
    tried = 0
    for description, chunk, targets in ranges:
        for name, function in HASHES:
            function = function or xor32
            if name == "xor32" and len(chunk) > 16 * 1024 * 1024:
                print(f"    skipped {name} over {len(chunk):,} bytes (too large to fold here)")
                continue
            value = function(chunk)
            tried += 1
            for position in targets:
                stored = data[position:position + 4]
                for order in ("big", "little"):
                    if int.from_bytes(stored, order) != value:
                        continue
                    matches += 1
                    print(f"    MATCH  {name} over {description} ({order}-endian) "
                          f"= 0x{value:08X}, stored at {position:#x}")
    print()
    if matches:
        print(f"  {matches} match(es). A match is a candidate, not a proof: confirm by changing one")
        print("  byte in a copy and seeing whether the title rejects it (E3).")
    else:
        print(f"  no match in {tried} attempt(s).")
        print("  That does not prove the save has no integrity value. It may be a different")
        print("  algorithm (a console title often uses a custom CRC table), a different range,")
        print("  or narrower than a word. E2 is what decides: if a single changed byte in a copy")
        print("  makes the title refuse the save, there is a check somewhere even if this did")
        print("  not find it -- and the refusal is the finding to report.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("survey", help="entropy, ASCII runs and record strides")
    p.add_argument("file")
    p.add_argument("--block", type=int, default=4096, help="block size in bytes (default 4096)")
    p.add_argument("--max-blocks", type=int, default=48, help="blocks to print (default 48)")
    p.add_argument("--max-strings", type=int, default=24, help="ASCII runs to print (default 24)")
    p.add_argument("--stride-max", type=int, default=1024)
    p.add_argument("--samples", type=int, default=4096, help="sample positions per stride")
    p.add_argument("--top", type=int, default=8)
    p.set_defaults(run=cmd_survey)

    p = sub.add_parser("checksums", help="is a word in the header or trailer a hash of the rest?")
    p.add_argument("file")
    p.set_defaults(run=cmd_checksums)

    args = parser.parse_args(argv)
    if not os.path.isfile(args.file):
        raise SystemExit(f"not a file: {args.file}")
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
