#pragma once
// Where in the guest a kernel import was called from.
//
// A trace that says "save0:\savegame.svg was written" does not say which of the
// title's functions wrote it, and that function is the one to look at in the
// recompiled tree. The guest's return address is in `lr` at the call; the frames
// above it are reached through the back chain the PowerPC ABI keeps in the first
// word of each frame, the same walk pacing_trace.cpp makes.
//
// That chain is a guess about memory the title owns, and a wrong guess would
// fault the host in the middle of tracing a save. So every step is checked
// before it is read: the frame has to be inside the guest's own windows, aligned,
// and reachable from the one below it. A walk that stops early is fine -- three
// addresses are for finding a function, not for reproducing a crash.
#include <cstdint>

#include "guest.h"
#include "guest_memory.h"

namespace kernel
{
    // Innermost first. A zero means there is nothing more.
    struct GuestStack
    {
        uint32_t addresses[4] = { 0, 0, 0, 0 };
    };

    // Whether `address` can be a stack pointer at all: the bank's two windows,
    // where every thread stack is allocated.
    inline bool PlausibleFrame(uint32_t address)
    {
        if (!address || (address & 3u)) return false;
        return (address >= guest::kHeapBase && address < guest::kApertureBase);
    }

    // `lr` as called, then up to three callers. Reads nothing it has not first
    // decided is inside the guest's memory, and stops at the first frame that is
    // not. Call it with the import's own context (ctx.r1, ctx.lr).
    inline GuestStack CaptureGuestStack(uint32_t r1, uint32_t lr)
    {
        GuestStack out;
        uint32_t used = 0;
        if (lr) out.addresses[used++] = lr;

        // Each frame's back chain is the stack pointer of the frame above it, and
        // that frame has its return address eight bytes below its own pointer.
        uint32_t frame = r1;
        while (used < 4 && PlausibleFrame(frame))
        {
            const uint32_t caller = uint32_t(*GuestPtr<be32>(frame));
            // Forward only, and by no more than one frame can be, so a garbage
            // word cannot send this off across the address space.
            if (caller <= frame || caller - frame > 0x10000u) break;
            if (caller < 8 || !PlausibleFrame(caller)) break;
            out.addresses[used++] = uint32_t(*GuestPtr<be32>(caller - 8));
            frame = caller;
        }
        return out;
    }
}
