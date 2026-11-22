# Career editing research: Master League and Become a Legend

What can be established about PES 2017's career data **without running the game**,
what the port already guarantees about saves, and the exact experiments that need
a device. Nothing here ships a game file, and nothing here claims a field it has
not seen.

Status: **research tooling and analysis only.** No editor is implemented, because
no career field has yet been located in real data. That is deliberate — see
"Rules" at the bottom.

---

## 1. What is already established, and how

### 1.1 A save is a directory holding the title's own file

`runtime/kernel/content.cpp` implements `XamContentCreateEx`: the title asks XAM to
mount a content package as a drive (`save0:`), then opens `save0:\savegame.svg`
with the ordinary file API and reads or writes it like any other file. On the
console a package is an STFS container; this runtime makes it a directory under
`saves/` instead. Its header comment is explicit that nothing here writes STFS,
and that "the title never looks inside the container".

Consequences, all from that source:

- The bytes an editor would touch are exactly the bytes the title wrote. There is
  no wrapper, no host-side signature and no host-side checksum to preserve.
- `kernel::ListContent` enumerates packages by reading a `.xcontent` record saved
  beside the file, which is where the display name and content type live.
- `RotatePackage` moves a replaced package aside as `<name>.bak`, so a whole save
  survives under one name or the other.
- `android/.../SaveBackup.kt` already backs up the whole `saves/` folder by zip and
  restores it through a staging directory, keeping `saves.before-restore-<time>`.
  Its own comment names what it covers: Master League, Become a Legend, edit data,
  the system data and the profile. So the runtime does produce those packages.

### 1.2 The save write path is already crash-safe

`runtime/kernel/durable.cpp` exists for exactly the failure mode this task worries
about, and its header states the reasoning: `fflush` only reaches the page cache,
and truncating a file to rewrite it leaves a window with no whole file in it.

`kernel::WriteFileAtomically` therefore writes `path.tmp`, `fsync`s it, rotates the
previous contents to `path.bak`, `rename`s the temporary over the target, and
`fsync`s the directory. `kernel::SyncStream` is the `fflush` + `fsync` pair, and
`NtFlushBuffersFile` (`runtime/kernel/files.cpp`) calls it — so when the title asks
for its save to be on disk, it is.

The title does its own version of the same dance: `NtSetInformationFile` handles
`FileRenameInformation` with a "replace if exists" flag, which is the mechanism a
game uses to publish a finished save over the old one. (The comment there says
`data.tmp` → `data.bin`; that comment is inherited phrasing, and **which names PES
actually uses is unverified** — only the mechanism is.)

**So the editor does not need to reimplement atomic saving.** What it must not do is
write `savegame.svg` behind the title's back while the title holds the career in
memory: the live copy would win on the next save and silently undo the edit. The
write path is therefore "change the value in the title's live structure, then let
the player save with PES's own save function".

### 1.3 What is *not* established

- The layout of `savegame.svg`: header, size, field order, any internal checksum,
  how many careers a package holds, and where one ends and the next begins.
- Which package directory belongs to which career.
- Whether budgets are integers, scaled integers (PES often uses thousands), or
  floats; and the same for BAL attributes, skill points and progression.
- Anything at the function level: `runtime/title.h` defines only twelve guest
  addresses (entry point, `D3D_Present`, `BlockOnFence`, two aspect floats, two
  import records, …) and **none of them is save- or career-related**. Save hooks are
  compiled out (`GUEST_HOOK` on an undefined address is skipped), so no guest
  function in the save path is hooked by name. What can be had instead is where
  each save operation was called from: `PES17_TRACE_CAREER` (§3) records the guest
  return address and up to three callers with every mount, open, read, write and
  rename.

Two things explain why the executable itself is not in this workspace:

- `pes/default.xex` and `pes/default.pe` are gitignored game data. The decoded PE is
  produced by `tools/xexdump.py` from *your* dump.
- `ppc/` — the ~2.2M lines of recompiled C++ — is **generated** by `build.sh` and
  gitignored too. What is committed is the runtime, the launcher and the tools. So
  "the recompiled engine source" here means the hand-written host runtime plus a
  generated tree that only exists after a build.

---

## 2. The tools

Both run on ordinary data with no game, no device and no disc.

### `save_probe.py` — look inside saves/

```bash
tools/career/save_probe.py list  /path/to/saves
tools/career/save_probe.py info  /path/to/saves/<package>/savegame.svg
tools/career/save_probe.py diff  before.svg after.svg
tools/career/save_probe.py find  career.svg 500000 --width u32 --order be
```

- `list` — every package and file, with the rotated `.bak` copies marked and the
  title's own save called out.
- `info` — size, SHA-256, head and tail hexdump, first words read every way.
- `diff` — every byte two saves disagree on, as runs, with each side decoded as
  u8/16/32/64, signed, and f32/f64 in both byte orders, plus the enclosing 32-bit
  word. This is the workhorse: a diff whose cause is known *is* an offset finding.
- `find` — locate a value you can already read in the game, at any width and byte
  order, optionally inside an offset range.

### `xex_career_map.py` — find the save code in your own executable

```bash
python3 tools/xexdump.py path/to/default.xex pes/default.pe   # your dump
tools/career/xex_career_map.py --strings savegame,save0,Memcard
tools/career/xex_career_map.py --symbols-out config/pes_symbols.txt
```

It scans the flat PE for printable runs containing a keyword, finds the code that
materialises each string address (a literal word, or the `lis`+`addi`/`ori` pair the
compiler emits), maps each reference to the `.pdata` function containing it, and
prints the identifier to grep in the generated tree:

```
8200A1C4  'savegame.svg'   2 reference(s)
    8234ABCD -> sub_8234AB00 +0xCD   grep: sub_8234AB00
```

`--symbols-out` writes `ADDR name` lines in the format `tools/title.py` already
reads (`config/pes_symbols.txt`), so from then on every tool prints
`sub_8234AB00 (ref_savegame_svg)`. Note `*.txt` is gitignored here.

It reuses `tools/title.py`'s own section and `.pdata` reader rather than a second
parser, so it sees the image the same way the recompiler's tooling does.

### `test_career_tools.py`

```bash
python3 tools/career/test_career_tools.py
```

Builds a synthetic flat PE (a `lis`/`addi` reference to a string, two `.pdata`
functions) and a `saves/` folder holding two copies of a career differing in one
32-bit word, then asserts what the tools report. 15 checks, all passing; an
analyser that has never been run is not evidence.

---

## 3. `PES17_TRACE_CAREER` — what the title does to its saves

This is the missing half of the static pass: the sequence. A menu action mounts a
package, opens `save0:\savegame.svg` inside it, reads or writes it, and publishes a
finished save by renaming the temporary over it. Nothing in the runtime recorded
that before, and it is what identifies Master League's and Become a Legend's
data without guessing at either.

Implementation: `runtime/kernel/career_trace.h` and `.cpp`, hooked where the save
system already is — the content layer (`runtime/kernel/content.cpp`) for packages
and the file layer (`runtime/kernel/files.cpp`) for the operations inside one.
Disc files are not traced at all, so a run with this on does not record the game
streaming its own data.

### Turning it on

| where | how |
|---|---|
| desktop | `PES17_TRACE_CAREER=1 ./build/pes17 …`, or the line `PES17_TRACE_CAREER=1` in the `.env` beside the executable |
| Android | `adb shell run-as com.pes2017.recomp touch files/career_trace` before starting the game; delete the file to stop. The app passes that file's existence in as the switch, so a trace run needs no rebuild |

The lines go to the ordinary log: `PES17_LOG_FILE=<path>` on desktop, the app's
own log screen (and logcat) on Android. They are written as they come and are not
folded into the log's repeat counts — a trace whose lines differ only in their
offsets is exactly what that folding would destroy. `PES17_LOG_ALL` is not needed.

### The line

```
career 5120341 mount path=save0: to=saves/SGJC0001 flags=0x00000006 note=existing stack=8234AB00,8200A1C4 thread=main thread
career 5120390 open path=save0:\savegame.svg to=saves/SGJC0001/savegame.svg size=142336 handle=0x0001000C note=existing, read-only stack=8234AB00,8200A1C4 thread=main thread
career 5120391 read path=save0:\savegame.svg to=saves/SGJC0001/savegame.svg size=4096 done=4096 at=0 handle=0x0001000C stack=8234AB00,8200A1C4 thread=guest thread 3
```

| field | meaning |
|---|---|
| `career <µs> <op>` | always. µs is since the runtime started |
| `path` | the drive (`save0:`), or the path as the guest named it |
| `to` | the host path — which names the package — a rename's target, or the `.bak` |
| `size`, `done`, `at` | bytes asked for, bytes moved, offset in the file. All three on `read` and `write`; `size` alone on `open`, `close`, `flush`, `truncate` |
| `handle` | the guest's handle, on everything that has one |
| `status` | NTSTATUS or XERROR, printed only when it is not zero |
| `flags` | `XamContentCreateEx`'s flags; the low nibble is the disposition |
| `note` | `new` / `emptied` / `existing` / `refused`, `created` / `overwritten` / `read-only`, `sequential`, … |
| `stack` | the guest return address that made the call, then up to three callers (`runtime/kernel/guest_stack.h`); a walk that cannot go further stops there |
| `thread` | the runtime thread |

Ops: `mount`, `unmount`, `rotate`, `delete`, `open`, `read`, `write`, `rename`,
`flush`, `close`, `truncate`.

Three things it deliberately does not do: it never records a byte of a save (only
paths, sizes, offsets and statuses), it has no way to write to one, and it is off
by default — with it off, every hook is one cached `bool` and a branch, and
nothing is formatted or logged.

### Reading the log, which is experiment E1

1. Start with tracing on and go straight to *Master League → Load*. The first
   `mount` line names the package (`to=saves/<name>`); every operation until the
   `unmount` is that career being read.
2. Back to the menu (`unmount`), then *Become a Legend → Load*. Its `mount` names
   the other package. If both modes mount the **same** package, that is a finding
   too: one container for both changes what an editor has to select.
3. `tools/career/save_probe.py list saves/` records both packages' files, sizes
   and hashes next to the trace.
4. The `stack` addresses are the save-and-load code. Name them with
   `tools/title.py` once `xex_career_map.py --symbols-out config/pes_symbols.txt`
   has run on your own `default.xex`, then grep the names in the generated tree.

A save — a match played or a week advanced, then *Save* — shows up as `open …
note=created, writable`, a run of `write`s, `flush`, then `rename` from the
temporary onto `savegame.svg`, then `close`. The rename is the moment a save
becomes real, and its two names say what the title calls its temporary.

### The checks

```bash
ctest --test-dir build-tests -R career-trace --output-on-failure
```

`tests/career_trace_test.cpp` checks the switch's rule, every op name, and the
line format byte for byte — including that an event keeps its own timestamp when
one is given. It needs no guest, save or device. What it cannot check is that a
line corresponds to a real save: that is E1, on your hardware.

---

## 4. The experiments that need the device

Each is small, and each names what it settles. Use disposable copies of careers —
never the only save.

**E1 — which package is which career.** Run with `PES17_TRACE_CAREER=1` (see §3
for the phone), enter *Master League → Load*, and note the `mount` line's `to=`
and every operation after it; repeat for *Become a Legend*. `tools/career/save_probe.py
list` then records the folder. Settles "identify the active career without
touching unrelated data". No file is modified.

**E2 — the serialized layout, by cause.** Copy one career twice. Change exactly one
thing in the game (advance a week; play a match so the budget moves a known
amount), save with PES's own save, quit. `diff` the copies. Every moved byte is now
attributable. Repeat with a second, orthogonal change to separate fields that move
together. This is how the offset gets established — never by assuming a stride.

**E3 — width, scale and range.** For each candidate: write it in a *copy*, load it,
and check the number the game shows. Then bisect the valid range (0, 1, mid, huge)
and record what the title accepts, clamps or rejects. A field that the title refuses
is documented as refused, not widened silently.

**E4 — the runtime structure and its update routine.** With `PES17_WATCH=<hex>` on
the field's runtime address, and `PES17_DUMP`/`PES17_WATCHDOG` for the loaded buffer,
find what reads it and from where (`docs/switches.md`). `tools/callgraph.py`,
`tools/find_in_title.py` and `tools/xex_career_map.py` name the candidates.
Settles the pointer chain and the function names, which is what makes an editor
possible at all.

**E5 — the required persistence test, per mode.** Load a career → edit → save
normally → force-stop → relaunch → reload → confirm the value **in the game's own
screen** → confirm a second career, the edit data and the system data still load.

**E6 — interrupted save.** Kill the process during a save; confirm `RotatePackage`'s
`.bak` rename-back recovers a whole save. Report the observed behaviour; do not
claim atomicity that was not observed.

A device note: `PES17_WINDOW=0` runs the title headless (useful for scripted
tracing), `PES17_FPS_LIMIT` and `PES17_NO_AUDIO` reduce heat, and
`PES17_INPUT_SCRIPT` presses pad buttons at wall-clock seconds, so E1 and E2 can be
driven without a human at the screen.

---

## 5. Rules these notes are written to

- A field enters an editor only after E2 and E3 agree on it and the game has shown
  the value back.
- No offset is ever assumed from a stride, another game, or the MW2 scaffold this
  port descends from (`launcher/playerdata_layout.h`, `profile.cpp`,
  `runtime/player.cpp` are all MW2 code and are not evidence about PES).
- The editor modifies the title's live career and lets PES save it. It does not
  write a second store, and it does not rewrite a save the game holds in memory.
- Every write is backed up first (package copy, then the title's own rotation), and
  restored if the operation fails.
- A feature that has not survived E5 is reported as untested, not shipped.
