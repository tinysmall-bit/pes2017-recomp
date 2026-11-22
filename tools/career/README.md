# Career editing research: Master League and Become a Legend

What can be established about PES 2017's career data **without running the game**,
what the port already guarantees about saves, and the exact experiments that need
a device. Nothing here ships a game file, and nothing here claims a field it has
not seen.

Status: **research tooling and analysis only.** No editor is implemented, because
no career field has yet been located in real data. E1 and E2 are instrumented and
written up in §4, but neither has been run on hardware yet, and no offset below is
claimed. That is deliberate — see "Rules" at the bottom.

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

### `trace_report.py` — read the trace, get E1's answer

```bash
tools/career/trace_report.py reports/career/e1/master-league.log
tools/career/trace_report.py --timeline reports/career/e1/*.log
adb logcat -d | tools/career/trace_report.py -
```

Groups the `career` lines into the sessions between a `mount` and its `unmount`
and prints, per session: the package, the drive, the mount's flags and note, how
many operations of each kind, bytes read and written, every `open` with its size
and note, the guest stacks that made the calls, any `rename` (a save being
published), and the unmount time. Then a rollup of the packages *in mount order*,
which is the identification: one mode per capture, and the package it mounted is
that mode's save.

It is deliberately fussy about the three ways a trace is misread: a session with
no `unmount` is reported as open-ended rather than as a short session; a non-zero
`status` is counted and shown instead of hidden (the title probing for a file it
does not have is `0xC0000034`, not a failure); and a `stop` line — the 250,000
event cap — is called out, because the log is then not the whole run.

### `save_layout.py` — is the file plain, and is there a checksum?

```bash
tools/career/save_layout.py survey    savegame.svg
tools/career/save_layout.py checksums savegame.svg
```

`survey` answers the question that decides whether a field editor is even
possible: entropy per block, zeros, surviving ASCII runs, and a record-stride scan.
A save that is deflate-compressed or encrypted is dense and flat (≈8 bits/byte, no
ASCII) and **cannot** be patched by offset at all — the editor would have to work
on the title's live structure instead. The stride scan reports how often a 2-byte
pair repeats at each stride against the median over every stride tried, which is
what chance looks like in that file; a peak says a fixed-size record repeats, and
says nothing about where a record begins or which offset is which field.

`checksums` takes the plausible places for an integrity value (the last word, the
last two, the first dwords of a header) and asks whether any equals crc32,
adler32, a byte sum or a word xor of some other range of the same file — including
the file with that word zeroed. It prints every attempt and its negative result:
"no match" is **not** "no checksum". A match is a candidate, and only the title's
behaviour confirms it.

### The tool checks

```bash
python3 tools/career/test_career_tools.py       # 15 checks
python3 tools/career/test_career_analysis.py    # 24 checks
```

The first builds a synthetic flat PE (a `lis`/`addi` reference to a string, two
`.pdata` functions) and a `saves/` folder holding two copies of a career differing
in one 32-bit word, then asserts what the probe and the map report. The second
builds a `career` log in the emitter's exact format (two mount sessions, a failed
open, a truncated one, the cap) and a save-like file with a 0x40 record stride and
a trailing CRC32, then asserts what the report and the layout survey say. An
analyser that has never been run is not evidence — but neither is an analyser run
only on its own fixture: `tests/career_trace_test.cpp` pins the line format, and
the report is checked against a log written by the real `career_trace::Emit`, not
by a copy of its format.

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

### Reading the log

The commands that produce one of these logs are E1's, in §4; this is what the lines
mean in sequence. A `mount` line names the package (`to=saves/<name>`), and every
operation until the `unmount` is that career being touched. The `stack` addresses are
the save-and-load code: name them with `tools/title.py` once `xex_career_map.py
--symbols-out config/pes_symbols.txt` has run on your own `default.xex`, then grep
the names in the generated tree.

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

## 4. Running E1 and E2 on the device

Each experiment is small and each names what it settles. Both of the first two are
**read-only, and neither pushes anything back to the phone**: E1 switches the trace
on and reads the log, and E2 changes something through the game's own menus and
lets PES save it. Not one byte is sent to the device.

Before starting:

- **Take one full backup**, and do it while the game is stopped: Settings › Game
  files › Back up, or the `adb pull` in E1 step 1. Everything afterwards works on
  copies of what you pull; the phone's `saves/` stays the original.
- **Captures live under `reports/`**, which is gitignored. A save is private data,
  and a trace names its packages: neither belongs in a commit.

Where the files are, from the app's own code (`SaveBackup.savesDir`, `LogSummary`):

| what | path |
|---|---|
| `saves/` | beside the game folder — `…/files/saves/` for the default one; a directory per package, `<name>.bak` for the previous save a rotation kept aside |
| the log the trace goes to | the app's own folder, `files/game.log`, and logcat |

The paths below use `…/files/saves` for the first; substitute yours if the game
folder was moved. `P=com.pes2017.recomp` keeps the commands readable.

### E1 — which package is which career

```bash
P=com.pes2017.recomp
mkdir -p reports/career/e1

# 1. the saves as they are now (the backup this experiment needs, since it changes nothing)
adb pull /sdcard/Android/data/$P/files/saves reports/career/e1/saves-before
python3 tools/career/save_probe.py list reports/career/e1/saves-before

# 2. switch the trace on without a rebuild, and drop the previous log
adb shell run-as $P touch files/career_trace
adb shell run-as $P rm -f files/game.log

# 3. start the game. Go straight to Master League > Load, open a career, back out to the
#    menu, and stop the game. Nothing else — the first mount line should be that career.

# 4. the log, and the answer
adb shell run-as $P cat files/game.log > reports/career/e1/master-league.log
python3 tools/career/trace_report.py reports/career/e1/master-league.log
#    if game.log came back empty, the lines are on logcat instead:
adb logcat -d | python3 tools/career/trace_report.py -

# 5. the same for Become a Legend
adb shell run-as $P rm -f files/game.log
#    ... Become a Legend > Load, open a career, back out, stop ...
adb shell run-as $P cat files/game.log > reports/career/e1/become-a-legend.log
python3 tools/career/trace_report.py --timeline reports/career/e1/*.log
```

What the report settles, and how to read it:

- **One `mount` line, one package.** The `to=` value is that mode's save: for the
  session between the `mount` and its `unmount`, every `open`/`read`/`write` is
  that career being touched. `--timeline` over both logs shows the two packages in
  the order you mounted them, which is the whole identification.
- **Both modes on the same package** is also a finding, and it changes the plan:
  one container holds both, so an editor selects a career *inside* a package
  rather than picking a package per mode. Record it; do not force two names.
- The `stack` on the `mount` line is the load code. `xex_career_map.py
  --symbols-out config/pes_symbols.txt` on your own `default.xex` makes
  `tools/title.py` print those addresses as names.
- An `open … status=0xC0000034` is `STATUS_OBJECT_NAME_NOT_FOUND`: the title
  looking for a file it does not have. Record it and move on.
- Write down the `size=` on the `open` of `savegame.svg`. It is the length of one
  career's data, and it is the first thing E2's diff has to agree with.

`trace_report.py` warns when a session never ended, when an operation returned a
non-zero status, and when the 250,000-event cap was hit — a capped log is not the
whole run and must not be read as one.

### E2 — the layout, by cause

One change per capture, and a capture both before and after it.

```bash
P=com.pes2017.recomp
mkdir -p reports/career/e2-change-one
adb pull /sdcard/Android/data/$P/files/saves reports/career/e2-change-one/saves-before
```

Then, in the game, make **exactly one** change whose value you can read on screen —
that is what makes the moved bytes attributable:

- a Master League budget on the club screen, if it shows the number;
- a Become a Legend player's skill points or rating;
- or, crudest and most useful for a first pass, **advance one week with no match**:
  it moves a date and a counter and little else.

Save with **PES's own save** (do not copy anything into `saves/` by hand), quit the
game completely, then:

```bash
adb pull /sdcard/Android/data/$P/files/saves reports/career/e2-change-one/saves-after
python3 tools/career/save_probe.py diff \
    reports/career/e2-change-one/saves-before/<package>/savegame.svg \
    reports/career/e2-change-one/saves-after/<package>/savegame.svg \
  | tee reports/career/e2-change-one/diff.txt
```

Read the diff for four different things:

1. **The offsets that moved**, as runs, with the enclosing word in both byte orders.
   A budget of 500,000 → 999,999 moves three bytes inside one big-endian word and
   nothing else; that shape is the finding, and it must be reported with both values,
   not just the offset.
2. **Everything else in the same package that moved**, and what it looks like: a
   plausible checksum near the start or the end of the file is the next thing to
   investigate, not a nuisance.
3. **Whether the file changed length** (`diff` says so), which would mean the save is
   variable-length or has slack in it — a fixed offset then needs the record's start
   known, not just the field's.
4. **Whether another package was written.** The trace answers this directly: run the
   save with `files/career_trace` in place and `trace_report.py` lists every mount.
   A career save that also rewrites the profile or the system data is a dependency any
   editor has to respect, and this is the experiment that proves it rather than
   assuming it.

A second run with a different, orthogonal change is what separates fields that always
move together (a date and its counter; a value and a total) from the one you meant to
change. Bytes that move in *both* runs are shared state and must not be treated as the
field.

### Integrity, before any write is proposed

```bash
python3 tools/career/save_layout.py survey    reports/career/e2-change-one/saves-after/<package>/savegame.svg
python3 tools/career/save_layout.py checksums reports/career/e2-change-one/saves-after/<package>/savegame.svg
```

- `survey` decides whether a field editor is possible at all. Dense, flat data with no
  ASCII runs is compressed or encrypted: then no offset patch means anything, and the
  editor must work on the title's live structure. A visible record stride and surviving
  text is the opposite, and says the save is plain.
- `checksums` reports a **candidate**, never a proof. The proof is the title's own
  behaviour, and getting it needs a write: change one byte that is not the candidate
  checksum in a *copy pulled from the device*, put that copy back on a disposable slot,
  and see whether the game loads it. That is E3's territory, not E1/E2's, and it is
  recorded as "the title rejected a save whose check word did not match" only if it
  actually did.
- Two failures worth distinguishing and reporting exactly: a copy the title **refuses
  to load** (the check is on load), and a copy the title loads but that is **replaced
  by the unmodified one on the next save** (the check is on save, or the live structure
  won — see §1.2).

### What the offsets record looks like

No row may be added until E2 and E3 agree and the game has shown the value back. The
record is the evidence, not the conclusion — paste it into an issue or a note:

| offset | width | byte order | field | evidence | shown back in game |
|---|---|---|---|---|---|
| *(none yet — no career save has been read on a device)* | | | | | |

```
E2 change: <what you did, and the value on screen before/after>
file:      saves/<package>/savegame.svg   <size> -> <size>
package mounted for the save (from the trace): <to=>
moved:     <offset> <length>   A=<hex>  B=<hex>   word: A=<be/le>  B=<be/le>
unchanged but suspicious: <offsets that look like a counter, checksum, date>
other packages written: <none | names>
```

### E3 to E6, still ahead

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

A device note for the later experiments: `PES17_WINDOW=0` runs the title headless (useful for scripted
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
