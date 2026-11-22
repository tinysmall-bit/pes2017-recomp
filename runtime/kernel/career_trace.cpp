// See career_trace.h for what this is for and what a line means.
#include "career_trace.h"

#include "../diagnostics.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>

#ifdef PES17_ANDROID
#include "../android/android.h"
#endif

namespace
{
    // A cap, not a target: a run of this trace is a few hundred lines -- one
    // menu visit -- and this only exists so a title stuck in a save loop cannot
    // fill a phone's storage with one file.
    constexpr uint64_t kMaxEvents = 250000;

    std::atomic<uint64_t> g_events{ 0 };
    std::atomic<uint64_t> g_dropped{ 0 };

    std::mutex g_write;
    void (*g_sink)(const char* line) = nullptr;

    const std::chrono::steady_clock::time_point g_began = std::chrono::steady_clock::now();

    int64_t Microseconds()
    {
        return std::chrono::duration_cast<std::chrono::microseconds>(
                   std::chrono::steady_clock::now() - g_began).count();
    }

    // Deliberately not LOGK: a line that only differs from the one before it in
    // its offset is the point of this trace, and the log's repeat counting
    // (log.h, Repeat) would fold exactly those into "(x412)". Android has no
    // stderr, so there the line goes through the same logcat-and-file path every
    // other line does.
    void Write(const std::string& line)
    {
#ifdef PES17_ANDROID
        android::LogLine('k', line.c_str());
#else
        std::fprintf(stderr, "%s\n", line.c_str());
#endif
    }
}

bool career_trace::Enabled(const char* value)
{
    // Exactly env::Flag's rule (runtime/env.h): set to anything but "" or "0".
    return value && *value && !(value[0] == '0' && value[1] == 0);
}

bool career_trace::On()
{
    // Read once. This is tested before every read and write inside a package, so
    // it is a load and a branch when the trace is off, and nothing is built.
    static const bool on = diag::kOn && Enabled(std::getenv("PES17_TRACE_CAREER"));
    return on;
}

const char* career_trace::OpName(Op op)
{
    switch (op)
    {
    case Op::Mount:    return "mount";
    case Op::Unmount:  return "unmount";
    case Op::Rotate:   return "rotate";
    case Op::Delete:   return "delete";
    case Op::Open:     return "open";
    case Op::Read:     return "read";
    case Op::Write:    return "write";
    case Op::Rename:   return "rename";
    case Op::Flush:    return "flush";
    case Op::Close:    return "close";
    case Op::Truncate: return "truncate";
    }
    return "?";
}

std::string career_trace::Format(const Event& event)
{
    char head[80];
    std::snprintf(head, sizeof head, "career %lld %s", (long long)event.atUs,
                  OpName(event.op));
    std::string line(head);

    auto text = [&](const char* key, const std::string& value)
    {
        if (value.empty()) return;
        line += ' ';
        line += key;
        line += '=';
        line += value;
    };
    auto number = [&](const char* key, uint64_t value)
    {
        char field[48];
        std::snprintf(field, sizeof field, " %s=%llu", key, (unsigned long long)value);
        line += field;
    };
    auto hex = [&](const char* key, uint32_t value)
    {
        char field[48];
        std::snprintf(field, sizeof field, " %s=0x%08X", key, value);
        line += field;
    };

    text("path", event.path);
    text("to", event.to);

    switch (event.op)
    {
    // A read or a write is only interpretable with all three: what was asked
    // for, what happened, and where in the file. A read of nothing at the end of
    // a file is `size=N done=0`, which is a different thing from not reading.
    case Op::Read:
    case Op::Write:
        number("size", event.size);
        number("done", event.done);
        number("at", event.at);
        break;
    case Op::Open:
    case Op::Close:
    case Op::Flush:
    case Op::Truncate:
        number("size", event.size);
        break;
    default:
        break;
    }

    if (event.handle) hex("handle", event.handle);
    if (event.status) hex("status", event.status);
    if (event.flags) hex("flags", event.flags);

    text("note", event.note);

    std::string frames;
    for (uint32_t address : event.stack.addresses)
    {
        if (!address) continue;
        char one[16];
        std::snprintf(one, sizeof one, "%s%08X", frames.empty() ? "" : ",", address);
        frames += one;
    }
    text("stack", frames);

    text("thread", event.thread ? event.thread : "");
    return line;
}

bool career_trace::Emit(const Event& event)
{
    if (!On()) return false;

    const uint64_t n = g_events.fetch_add(1, std::memory_order_relaxed);
    if (n >= kMaxEvents)
    {
        if (g_dropped.fetch_add(1, std::memory_order_relaxed) == 0)
        {
            std::lock_guard held(g_write);
            const std::string stop = "career " + std::to_string(Microseconds()) +
                " stop size=" + std::to_string(kMaxEvents) +
                " note=the cap was reached, so the rest of this run is not traced";
            if (g_sink) g_sink(stop.c_str());
            else Write(stop);
        }
        return false;
    }

    Event stamped = event;
    if (stamped.atUs < 0) stamped.atUs = Microseconds();
    const std::string line = Format(stamped);

    std::lock_guard held(g_write);
    if (g_sink) g_sink(line.c_str());
    else Write(line);
    return true;
}

void career_trace::SetSink(void (*sink)(const char* line))
{
    std::lock_guard held(g_write);
    g_sink = sink;
}
