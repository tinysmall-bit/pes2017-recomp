#!/usr/bin/env python3
"""Checks for tools/career/ without the game, a device or the retail executable.

Both tools are testable on synthetic input, which is the point: an analyser that
has never been run is not evidence. This builds a flat PE the way the repo's
reader (tools/title.py) expects one -- file offset == guest address - 0x82000000,
a .pdata entry per function, a lis/addi pair pointing at a string -- and a saves/
folder holding two copies of a career that differ in one known 32-bit word. Then
it asserts that the tools report exactly those.

    python3 tools/career/test_career_tools.py

Nothing here reads the game's own files.
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
BASE = 0x82000000
PY = sys.executable

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        failures.append(name)


# ---- a flat PE with the shape title.py reads ------------------------------

def lis(reg, value):
    return 0x3C000000 | (reg << 21) | (value & 0xFFFF)          # addis rD, 0, hi
def addi(reg, value):
    return 0x38000000 | (reg << 21) | (reg << 16) | (value & 0xFFFF)
def nop():
    return 0x60000000
def blr():
    return 0x4E800020


def flat_pe(sections):
    size = max(va - BASE + len(data) for _name, va, data in sections)
    img = bytearray(b"\0" * size)
    struct.pack_into("<I", img, 0x3C, 0x80)
    img[0x80:0x84] = b"PE\0\0"
    file_header = 0x84
    optional_size = 0xE0
    struct.pack_into("<H", img, file_header + 2, len(sections))      # NumberOfSections
    struct.pack_into("<H", img, file_header + 16, optional_size)     # SizeOfOptionalHeader
    table = file_header + 20 + optional_size
    for i, (name, va, data) in enumerate(sections):
        entry = table + i * 40
        img[entry:entry + 8] = name.encode().ljust(8, b"\0")
        # VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData
        struct.pack_into("<IIII", img, entry + 8, len(data), va - BASE, len(data), va - BASE)
    for _name, va, data in sections:
        offset = va - BASE
        img[offset:offset + len(data)] = data
    return bytes(img)


def pdata_entry(start, size):
    return struct.pack(">II", start, ((size // 4) & 0x3FFFFF) << 8)


def build_fixture(folder):
    string_address = 0x82003000
    text_address = 0x82001000
    words = [lis(3, string_address >> 16), addi(3, string_address & 0xFFFF),
             nop(), nop(), blr()]
    words += [nop()] * (0x100 // 4 - len(words))
    text = b"".join(struct.pack(">I", w) for w in words)

    rdata = b"savegame.svg\0" + b"\0" * (0x100 - 13)
    function_start = 0x82002000
    functions = [nop(), nop(), nop(), blr()]
    functions += [nop()] * (0x100 // 4 - len(functions))
    code2 = b"".join(struct.pack(">I", w) for w in functions)
    pdata = pdata_entry(text_address, len(text)) + pdata_entry(function_start, len(code2)) + b"\0" * 8

    img = flat_pe([
        (".text", text_address, text),
        (".text2", function_start, code2),   # second range, addressed by its own entry
        (".rdata", string_address, rdata),
        (".pdata", 0x82004000, pdata),
    ])
    path = os.path.join(folder, "fixture.pe")
    with open(path, "wb") as f:
        f.write(img)
    return path


# ---- a saves/ folder with one known difference ---------------------------

BUDGET_OFFSET = 0x120
BUDGET_A = 500_000
BUDGET_B = 999_999


def build_saves(folder):
    saves = os.path.join(folder, "saves")
    package = os.path.join(saves, "ml-save-one")
    os.makedirs(package)
    payload = bytearray(b"\x00" * 0x200)
    payload[0:8] = b"PES17SVG"                              # pretend magic, for `info`
    struct.pack_into(">I", payload, BUDGET_OFFSET, BUDGET_A)
    struct.pack_into(">I", payload, 0x140, 12_500_000)      # another field, unchanged
    with open(os.path.join(package, "savegame.svg"), "wb") as f:
        f.write(payload)

    changed = bytearray(payload)
    struct.pack_into(">I", changed, BUDGET_OFFSET, BUDGET_B)
    path_b = os.path.join(folder, "career-after-save.svg")
    with open(path_b, "wb") as f:
        f.write(changed)

    rotated = os.path.join(saves, "ml-save-one.bak")
    os.makedirs(rotated)
    with open(os.path.join(rotated, "savegame.svg"), "wb") as f:
        f.write(payload)
    return saves, os.path.join(package, "savegame.svg"), path_b


def run(*args):
    result = subprocess.run([PY, *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"--- {args[0]} failed ({result.returncode}) ---")
        print(result.stdout)
        print(result.stderr)
    return result


def main():
    with tempfile.TemporaryDirectory() as folder:
        image = build_fixture(folder)
        saves, before, after = build_saves(folder)

        # ---- xex_career_map.py
        result = run(os.path.join(HERE, "xex_career_map.py"),
                     "--image", image, "--strings", "savegame")
        out = result.stdout
        check("xex_career_map finds the string", "savegame.svg" in out, out[:400])
        check("xex_career_map resolves a lis/addi reference",
              "82001000 -> sub_82001000" in out, out[:600])
        check("xex_career_map attributes it to a .pdata function",
              "sub_82001000" in out, out[:600])
        check("xex_career_map offers a name to grep for", "ref_savegame" in out, out[:800])

        symbols = os.path.join(folder, "pes_symbols.txt")
        run(os.path.join(HERE, "xex_career_map.py"),
            "--image", image, "--strings", "savegame", "--symbols-out", symbols)
        written = open(symbols).read() if os.path.exists(symbols) else ""
        check("xex_career_map writes a title.py-compatible sidecar",
              "82001000" in written and "ref_savegame" in written, written[:300])

        # ---- save_probe.py list
        out = run(os.path.join(HERE, "save_probe.py"), "list", saves).stdout
        check("save_probe list shows the package", "ml-save-one" in out, out)
        check("save_probe list marks the rotated save", ".bak" in out, out)
        check("save_probe list marks the title's save", "the title's save" in out, out)

        # ---- info
        out = run(os.path.join(HERE, "save_probe.py"), "info", before).stdout
        check("save_probe info reports the size", "512" in out, out)
        check("save_probe info reports a sha256", "sha256" in out, out)

        # ---- diff
        out = run(os.path.join(HERE, "save_probe.py"), "diff", before, after).stdout
        # 500,000 (0x0007A120) and 999,999 (0x000F423F) share a top byte of zero,
        # so three bytes move, starting one byte into the word: that is exactly what
        # a budget edit looks like, and the tool must still report the field.
        check("save_probe diff finds the changed bytes",
              f"{BUDGET_OFFSET + 1:#010x}" in out, out)
        check("save_probe diff reports three bytes in one run",
              "3 differing byte(s) in 1 run(s)" in out, out)
        check("save_probe diff decodes the enclosing word both ways",
              str(BUDGET_A) in out and str(BUDGET_B) in out, out)

        # ---- find
        out = run(os.path.join(HERE, "save_probe.py"), "find", after,
                  str(BUDGET_B), "--width", "u32", "--order", "be").stdout
        check("save_probe find locates the value at its offset",
              f"{BUDGET_OFFSET:#010x}" in out, out)

        out = run(os.path.join(HERE, "save_probe.py"), "find", after,
                  "77", "--width", "u8", "--order", "be").stdout
        check("save_probe find says so when a value is absent", "no 77" in out, out)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
