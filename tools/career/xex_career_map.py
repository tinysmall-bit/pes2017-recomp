#!/usr/bin/env python3
"""Find the save/career code in the executable and name it for the recompiled tree.

The recompiler names every function after its guest address (sub_82345678) and
nothing else, so the only way to talk about "the function that opens the save
package" is to locate it in the image first and then write that name down.
tools/title.py already reads a sidecar of `ADDR  name` lines
(config/pes_symbols.txt) and prints sub_XXXXXXXX (name) from it; this tool
produces those lines.

It works on the flat PE that tools/xexdump.py decodes from the retail XEX, so it
needs the user's own disc dump -- nothing here ships or fetches the game:

    GAME=<folder with default.xex> ./build.sh    # or, for the image alone:
    python3 tools/xexdump.py pes/default.xex pes/default.pe

    tools/career/xex_career_map.py                       # the save/career keywords
    tools/career/xex_career_map.py --strings save0,Memcard
    tools/career/xex_career_map.py --symbols-out config/pes_symbols.txt

For each keyword it prints every string in the image, the code addresses that
refer to it, and the .pdata function each of those sits in (the identifier to
grep for in the generated ppc/ tree, which .gitignore keeps out of the repo).

What this cannot tell you: which of those functions reads a budget, or where a
career struct's field lives. That needs the live game (docs/career.md, Phase 3).
This narrows the search to a handful of functions and makes them nameable; it
does not name a field.
"""
import argparse
import bisect
import os
import struct
import sys

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import title  # noqa: E402  (the repo's own image/section/function reader)

# Strings worth finding in a football game's executable. The title's own menu
# text lives in the .cpk archives, not here, so these are the plumbing names:
# the save API it calls and the words it puts in a path.
DEFAULT_STRINGS = [
    "savegame", "save0", "Memcard", "memcard", "savegame.svg",
    "ML",  # too short to be useful alone; kept out of the default pass below
]


def image(path):
    if not os.path.exists(path):
        raise SystemExit(
            f"no decoded image at {path}\n"
            "  make one from your own disc dump (never fetched by this repo):\n"
            "    python3 tools/xexdump.py path/to/default.xex pes/default.pe")
    with open(path, "rb") as f:
        return f.read()


def strings(img, keywords, minimum=4):
    """(guest address, text) for printable runs containing a keyword."""
    out = []
    runs, start = [], None
    for i, byte in enumerate(img):
        printable = 32 <= byte < 127
        if printable and start is None:
            start = i
        elif not printable and start is not None:
            if i - start >= minimum:
                runs.append((start, i))
            start = None
    if start is not None and len(img) - start >= minimum:
        runs.append((start, len(img)))

    lowered = [k.lower() for k in keywords]
    for begin, end in runs:
        text = img[begin:end].decode("latin-1")
        low = text.lower()
        if any(k in low for k in lowered):
            out.append((title.BASE + begin, text))
    return out


def references(img, target):
    """Code addresses that materialise `target`: a literal word, or lis+addi/ori."""
    code, size = title.text(img)
    body = img[code - title.BASE: code - title.BASE + size]
    out = set()

    literal = struct.pack(">I", target & 0xFFFFFFFF)
    at = body.find(literal)
    while at >= 0:
        if at % 4 == 0:
            out.add(code + at)
        at = body.find(literal, at + 1)

    count = len(body) // 4
    words = struct.unpack(f">{count}I", body[:count * 4])
    for i in range(count - 1):
        word = words[i]
        if (word >> 26) != 15 or ((word >> 16) & 0x1F) != 0:
            continue                        # not `lis rD, hi`
        reg = (word >> 21) & 0x1F
        hi = word & 0xFFFF
        nxt = words[i + 1]
        opcode = nxt >> 26
        if opcode not in (14, 24):          # addi / ori
            continue
        if ((nxt >> 21) & 0x1F) != reg or ((nxt >> 16) & 0x1F) != reg:
            continue
        lo = nxt & 0xFFFF
        if opcode == 14 and lo >= 0x8000:
            lo -= 0x10000
        value = (hi << 16) + lo
        if (value & 0xFFFFFFFF) == target:
            out.add(code + i * 4)
    return sorted(out)


def function_of(funcs, starts, address):
    """(start, offset) of the .pdata function holding `address`, or the nearest below."""
    at = bisect.bisect_right(starts, address) - 1
    if at < 0:
        return None, 0
    start = starts[at]
    size = funcs[start]
    if size and address < start + size:
        return start, address - start
    return start, address - start          # a leaf: .pdata has no entry for it


def slug(text):
    keep = [c if c.isalnum() else "_" for c in text]
    return "".join(keep).strip("_")[:40]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default=title.IMAGE,
                        help=f"flat PE (default {title.IMAGE})")
    parser.add_argument("--strings", default=None,
                        help="comma-separated keywords (default: the save API names)")
    parser.add_argument("--symbols-out", default=None,
                        help="write `ADDR name` lines here (tools/title.py reads "
                             "config/pes_symbols.txt)")
    args = parser.parse_args(argv)

    img = image(args.image)
    keywords = [k for k in (args.strings.split(",") if args.strings else DEFAULT_STRINGS)
                # "ML" alone matches half the image; require a longer token.
                if len(k) >= 4]
    if not keywords:
        raise SystemExit("no keyword of 4 characters or more")

    code_range = title.text(img)
    funcs = title.functions(img)
    starts = sorted(funcs)
    print(f"image {args.image}: code {code_range[0]:08X} +{code_range[1]:X}, "
          f"{len(funcs)} functions in .pdata")
    print(f"keywords: {', '.join(keywords)}\n")

    names = {}
    hits = strings(img, keywords)
    if not hits:
        print("no matching strings. The title's text lives in the .cpk archives, so a")
        print("keyword may simply not be in the executable; try --strings with a name")
        print("the save path would carry, or search the archives instead.")

    for address, text in hits:
        refs = references(img, address)
        print(f'{address:08X}  {text!r}   {len(refs)} reference(s)')
        for ref in refs:
            start, offset = function_of(funcs, starts, ref)
            label = title.label(start) if start is not None else "?"
            print(f"    {ref:08X} -> {label} +0x{offset:X}"
                  f"   grep: sub_{start:08X}" if start is not None else "")
            if start is not None:
                names.setdefault(start, f"ref_{slug(text) or 'str'}")
        print()

    if names:
        print(f"{len(names)} function(s) sit on at least one keyword:")
        for start in sorted(names):
            print(f"  {start:08X}  {names[start]}")
        if args.symbols_out:
            with open(args.symbols_out, "w") as f:
                f.write("# Written by tools/career/xex_career_map.py. Addresses only --\n")
                f.write("# a name here says 'this function mentions this string', which\n")
                f.write("# is a lead, not a verified role. Rename it once a device run\n")
                f.write("# (docs/career.md, Phase 3) has shown what it does.\n")
                for start in sorted(names):
                    f.write(f"{start:08X}  {names[start]}\n")
            print(f"\nwrote {args.symbols_out} ({len(names)} names); tools/title.py reads it")
    else:
        print("no function found on a keyword string")


if __name__ == "__main__":
    main()
