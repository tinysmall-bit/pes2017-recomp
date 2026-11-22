// What PES17_TRACE_CAREER puts in the log, and what it does when it is not set.
//
// The trace is only useful if a line can be read by something other than the
// run that printed it: the op names, the field order and the point at which a
// field is left out are the contract, and they are checked here byte for byte.
//
// What is not checked here, and cannot be: that a trace line corresponds to a
// real save. That needs the title, a package and a menu -- tools/career/README.md
// says which experiments settle it. Nothing in this file needs a disc, a guest,
// a device or a save; Emit's sink is where a check reads what a run would have
// written.
#include "kernel/career_trace.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace
{
    int g_failures = 0;
    int g_checks = 0;

    void Check(bool ok, const std::string& what)
    {
        g_checks++;
        if (ok) return;
        g_failures++;
        std::fprintf(stderr, "FAIL: %s\n", what.c_str());
    }

    void CheckLine(const std::string& got, const std::string& want, const std::string& what)
    {
        g_checks++;
        if (got == want) return;
        g_failures++;
        std::fprintf(stderr, "FAIL: %s\n  want: %s\n  got:  %s\n", what.c_str(),
                     want.c_str(), got.c_str());
    }

    std::vector<std::string> g_lines;
    void Capture(const char* line) { g_lines.emplace_back(line ? line : ""); }

    // What CaptureGuestStack hands an event, without a guest to walk.
    kernel::GuestStack guest_stack_of(uint32_t first = 0, uint32_t second = 0,
                                      uint32_t third = 0, uint32_t fourth = 0)
    {
        kernel::GuestStack out;
        out.addresses[0] = first;
        out.addresses[1] = second;
        out.addresses[2] = third;
        out.addresses[3] = fourth;
        return out;
    }

    // ---- the switch ------------------------------------------------------
    // The rule is env::Flag's (runtime/env.h): set to anything but "" or "0".
    // A variable nobody set, and one set to the empty string, are both off; so
    // is "0" and nothing else, which is why "00" is on -- the check is one
    // character, not a number.
    void TheSwitchIsEnvFlagsRule()
    {
        Check(!career_trace::Enabled(nullptr), "unset means off");
        Check(!career_trace::Enabled(""), "empty means off");
        Check(!career_trace::Enabled("0"), "\"0\" means off");
        Check(career_trace::Enabled("1"), "\"1\" means on");
        Check(career_trace::Enabled("yes"), "any other word means on");
        Check(career_trace::Enabled("00"), "only the single character 0 is off");
    }

    // ---- what the stack walk will follow ----------------------------------
    // The only part of guest_stack.h a check can reach without a guest: an
    // address that is not in the bank's windows and 4-byte aligned is never
    // dereferenced, which is what keeps a bad frame pointer from faulting the
    // host in the middle of tracing a save.
    void OnlyBankAddressesAreWalked()
    {
        Check(kernel::PlausibleFrame(0x84A00000), "a heap stack pointer is followed");
        Check(kernel::PlausibleFrame(0xB0001000), "a physical-window stack pointer is followed");
        Check(!kernel::PlausibleFrame(0), "null is not a frame");
        Check(!kernel::PlausibleFrame(0x84A00002), "a misaligned address is not a frame");
        Check(!kernel::PlausibleFrame(0x82000000), "the image below the heap is not a stack");
        Check(!kernel::PlausibleFrame(0x00100000), "the low address space is not a stack");
    }

    // ---- the op names ----------------------------------------------------
    // A log is read months later, by a tool or by hand, so these are part of the
    // format rather than a convenience.
    void EveryOpHasAStableName()
    {
        struct Named { career_trace::Op op; const char* name; };
        const Named table[] = {
            { career_trace::Op::Mount, "mount" },
            { career_trace::Op::Unmount, "unmount" },
            { career_trace::Op::Rotate, "rotate" },
            { career_trace::Op::Delete, "delete" },
            { career_trace::Op::Open, "open" },
            { career_trace::Op::Read, "read" },
            { career_trace::Op::Write, "write" },
            { career_trace::Op::Rename, "rename" },
            { career_trace::Op::Flush, "flush" },
            { career_trace::Op::Close, "close" },
            { career_trace::Op::Truncate, "truncate" },
        };
        for (const Named& entry : table)
            Check(std::string(career_trace::OpName(entry.op)) == entry.name,
                  std::string("op name: ") + entry.name);
    }

    // ---- the line --------------------------------------------------------

    // Which package a menu action touched is the fact the whole career
    // investigation rests on, so a mount line has to carry the drive, the host
    // package and the guest code that asked for it.
    void AMountNamesItsPackage()
    {
        career_trace::Event event;
        event.op = career_trace::Op::Mount;
        event.atUs = 1234;
        event.path = "save0:";
        event.to = "saves/BL";
        event.flags = 0x00000006;
        event.note = "existing";
        event.stack = guest_stack_of(0x8234AB00);
        event.thread = "main thread";
        CheckLine(career_trace::Format(event),
                  "career 1234 mount path=save0: to=saves/BL flags=0x00000006 "
                  "note=existing stack=8234AB00 thread=main thread",
                  "a mount line");
    }

    // A read is interpretable only with all three of what was asked, what
    // happened and where: a read of nothing at the end of a file is
    // size=N done=0, which is different from not reading.
    void AReadCarriesSizeDoneAndOffset()
    {
        career_trace::Event event;
        event.op = career_trace::Op::Read;
        event.atUs = 5000;
        event.path = "save0:\\savegame.svg";
        event.to = "saves/BL/savegame.svg";
        event.size = 4096;
        event.done = 4096;
        event.at = 0;
        event.handle = 0x0001000C;
        event.stack = guest_stack_of(0x80401234, 0x81FF0000);
        event.thread = "guest thread 3";
        CheckLine(career_trace::Format(event),
                  "career 5000 read path=save0:\\savegame.svg to=saves/BL/savegame.svg "
                  "size=4096 done=4096 at=0 handle=0x0001000C "
                  "stack=80401234,81FF0000 thread=guest thread 3",
                  "a read line");
    }

    // The title publishing a finished save. Both names are the point: the
    // temporary is the save in progress, the target is what it becomes.
    void ARenameNamesBothEnds()
    {
        career_trace::Event event;
        event.op = career_trace::Op::Rename;
        event.atUs = 9000;
        event.path = "saves/BL/savegame.svg.tmp";
        event.to = "saves/BL/savegame.svg";
        event.note = "replace";
        event.status = 0xC0000035;
        CheckLine(career_trace::Format(event),
                  "career 9000 rename path=saves/BL/savegame.svg.tmp "
                  "to=saves/BL/savegame.svg status=0xC0000035 note=replace",
                  "a rename line");
    }

    // A sequential write has no offset of its own; the note says so rather than
    // leaving a zero that reads like the start of the file.
    void ASequentialWriteIsMarked()
    {
        career_trace::Event event;
        event.op = career_trace::Op::Write;
        event.atUs = 9500;
        event.path = "save0:\\savegame.svg";
        event.size = 64;
        event.done = 64;
        event.note = "sequential";
        CheckLine(career_trace::Format(event),
                  "career 9500 write path=save0:\\savegame.svg size=64 done=64 at=0 "
                  "note=sequential",
                  "a sequential write line");
    }

    // Nothing but the fields the op has: an unmount is a drive and a time.
    void EmptyFieldsAreLeftOut()
    {
        career_trace::Event event;
        event.op = career_trace::Op::Unmount;
        event.atUs = 0;
        CheckLine(career_trace::Format(event), "career 0 unmount", "a bare unmount line");

        career_trace::Event closed;
        closed.op = career_trace::Op::Close;
        closed.atUs = 7000;
        closed.path = "save0:\\savegame.svg";
        closed.size = 100;
        closed.note = "writable";
        CheckLine(career_trace::Format(closed),
                  "career 7000 close path=save0:\\savegame.svg size=100 note=writable",
                  "a close line with no stack");
    }

    // A stack the walk stopped early is still a stack: zeros are not addresses.
    void AShortStackIsStillOneAddress()
    {
        career_trace::Event event;
        event.op = career_trace::Op::Flush;
        event.atUs = 1;
        event.size = 320;
        event.stack = guest_stack_of(0x8200A1C4, 0, 0, 0);
        CheckLine(career_trace::Format(event), "career 1 flush size=320 stack=8200A1C4",
                  "a one-frame stack");
    }

    // ---- emitting --------------------------------------------------------
    void EmitStampsTheTimeAndKeepsAnExplicitOne()
    {
        g_lines.clear();
        career_trace::Event open;
        open.op = career_trace::Op::Open;
        open.path = "save0:\\savegame.svg";
        Check(career_trace::Emit(open), "an event is emitted while tracing");
        Check(g_lines.size() == 1, "the sink saw one line");
        if (g_lines.size() == 1)
        {
            const std::string& line = g_lines[0];
            const std::string prefix = "career ";
            const bool stamped = line.rfind(prefix, 0) == 0 && line.size() > prefix.size() &&
                                 line[prefix.size()] >= '0' && line[prefix.size()] <= '9';
            Check(stamped, "the time is filled in when the caller left it out");
            Check(line.find('\n') == std::string::npos, "the sink gets no newline");
        }

        g_lines.clear();
        career_trace::Event explicitTime;
        explicitTime.op = career_trace::Op::Unmount;
        explicitTime.atUs = 42;
        explicitTime.path = "save0:";
        Check(career_trace::Emit(explicitTime), "the second event is emitted");
        Check(g_lines.size() == 1, "the sink saw the second line");
        if (g_lines.size() == 1)
            CheckLine(g_lines[0], "career 42 unmount path=save0:",
                      "an event's own timestamp is not overwritten");
    }

    // The format a reader depends on is the one Emit writes, not a parallel one.
    void EmitWritesWhatFormatProduces()
    {
        g_lines.clear();
        career_trace::Event event;
        event.op = career_trace::Op::Truncate;
        event.atUs = 77;
        event.path = "save0:\\savegame.svg";
        event.size = 512;
        event.note = "was 4096";
        career_trace::Emit(event);
        Check(g_lines.size() == 1, "the truncate is emitted");
        if (g_lines.size() == 1)
            CheckLine(g_lines[0], career_trace::Format(event),
                      "Emit hands over exactly Format's line");
    }
}

int main()
{
    TheSwitchIsEnvFlagsRule();

    // Set once, before anything asks On(): it caches the answer, as the call
    // sites do not have to read the environment on every read of a save file.
#ifdef _WIN32
    _putenv_s("PES17_TRACE_CAREER", "1");
#else
    setenv("PES17_TRACE_CAREER", "1", 1);
#endif
    Check(career_trace::On(), "PES17_TRACE_CAREER=1 turns the trace on");
    career_trace::SetSink(&Capture);

    OnlyBankAddressesAreWalked();
    EveryOpHasAStableName();
    AMountNamesItsPackage();
    AReadCarriesSizeDoneAndOffset();
    ARenameNamesBothEnds();
    ASequentialWriteIsMarked();
    EmptyFieldsAreLeftOut();
    AShortStackIsStillOneAddress();
    EmitStampsTheTimeAndKeepsAnExplicitOne();
    EmitWritesWhatFormatProduces();

    std::printf("%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? 0 : 1;
}
