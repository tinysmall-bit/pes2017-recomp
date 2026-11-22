#pragma once
// PES17_TRACE_CAREER: what the title does to its saved games, as it does it.
//
// The editor work needs one thing above all: which package a menu action touched,
// in what order, and by which guest function. Nothing in the runtime could say
// that before -- the save layer logged individual operations, filtered by count,
// with no notion of "this was Master League". This records the sequence.
//
// What is traced is the save system, not the game: a content package mounted as a
// drive (runtime/kernel/content.cpp) and every file operation inside one
// (runtime/kernel/files.cpp). Files on the disc are untouched by it, so a run
// with this on does not record the game streaming its own data.
//
//   PES17_TRACE_CAREER=1     one line per operation, as it happens
//
// A line is:
//
//   career <microseconds> <op> [path=...] [to=...] [size=N] [done=N] [at=N]
//                              [handle=0x...] [status=0x...] [flags=0x...]
//                              [note=...] [stack=ADDR,ADDR,...] [thread=...]
//
// `microseconds` is since the runtime started. `path` and `to` are paths as the
// layer knows them -- the guest's ("save0:\savegame.svg") and the host's
// ("saves/BL/savegame.svg") -- and the host one names the package, which is how a
// Master League save is told from a Become a Legend one without guessing at their
// contents. `stack` is the guest return address of the call and up to three
// callers (guest_stack.h), so a line can be taken to the recompiled function that
// caused it.
//
// What is never recorded: the bytes of a save, or any buffer the title passed.
// Only sizes, offsets, paths, handles and statuses. This module has no way to
// write to a save, and reading one through it is not possible either.
//
// Off -- the default, and every release build -- each call site does one cached
// load and a branch before it builds anything. On, lines are written as they come
// and are not folded into the log's repeat counts (log.h): a trace whose every
// line differs only in its numbers is exactly what that folding would destroy.
//
// See tools/career/README.md for what to do with the log: the experiments that
// identify Master League's and Become a Legend's packages and their save files.
#include <cstdint>
#include <string>

#include "guest_stack.h"

namespace career_trace
{
    enum class Op : char
    {
        Mount    = 'M',   // a content package mounted as a drive
        Unmount  = 'U',   // the drive released
        Rotate   = 'K',   // the previous package kept aside as <name>.bak
        Delete   = 'D',   // a package removed, or a file marked for deletion
        Open     = 'O',   // a file opened, inside a package only
        Read     = 'R',
        Write    = 'W',
        Rename   = 'N',   // how a save is published: temp renamed over the target
        Flush    = 'F',   // the title asking for its bytes to be on storage
        Close    = 'C',
        Truncate = 'T',   // FileEndOfFileInformation
    };

    struct Event
    {
        Op op = Op::Open;
        int64_t atUs = -1;        // -1: Emit stamps it
        std::string path;         // guest path, drive, or host path
        std::string to;           // host path, rename target, backup name
        std::string note;         // "new", "read-only", "sequential", ...
        uint64_t size = 0;        // bytes asked for, or the file's size
        uint64_t done = 0;        // bytes actually moved (read/write)
        uint64_t at = 0;          // offset in the file (read/write)
        uint32_t handle = 0;
        uint32_t status = 0;      // NTSTATUS, printed when not zero
        uint32_t flags = 0;       // XamContentCreateEx's flags
        kernel::GuestStack stack;  // who called, innermost first
        const char* thread = nullptr;
    };

    // The rule On() applies: set, and not "" or "0". The same rule as env::Flag.
    bool Enabled(const char* value);

    // Whether PES17_TRACE_CAREER is set, in a build that has diagnostics. Read
    // once: the environment does not change under a run.
    bool On();

    // "mount", "read", ... -- stable, for the log and for whatever reads it.
    const char* OpName(Op op);

    // The line, without a newline.
    std::string Format(const Event& event);

    // Formats and writes the line. False when tracing is off, or when the event
    // count has reached its cap (so a run cannot fill a phone's storage).
    bool Emit(const Event& event);

    // Where a line goes. Null -- the default -- writes it to the log: stderr on a
    // desktop, logcat and the app's own log on Android. The checks in tests point
    // it at a buffer instead.
    void SetSink(void (*sink)(const char* line));
}
