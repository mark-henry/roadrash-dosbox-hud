#!/usr/bin/env python3
"""
Caps Lock -> telemetry mark (hands-free event marking while driving).

Toggle Caps Lock during an event (the keyboard LED shows the state); this sets the
`mark` flag in reader.py's telemetry.csv via its /mark endpoint. The game ignores
Caps Lock, so it won't affect driving. Runs WITHOUT sudo, in your GUI session.

    python3 marker.py            # talks to reader at localhost:8723
    python3 marker.py 8723
"""
import ctypes, time, sys, urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8723
cg = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
cg.CGEventSourceFlagsState.argtypes = [ctypes.c_uint32]
ALPHASHIFT = 0x00010000   # kCGEventFlagMaskAlphaShift = Caps Lock

def caps_on():
    return bool(cg.CGEventSourceFlagsState(0) & ALPHASHIFT)

print(f"Caps Lock -> mark (reader on :{PORT}). Toggle Caps Lock to mark events. Ctrl-C to quit.")
last = None
while True:
    c = caps_on()
    if c != last:
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/mark?v={1 if c else 0}", timeout=1).read()
            sys.stdout.write(f"\rmark {'ON ' if c else 'off'}   "); sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(f"\n(reader not reachable on :{PORT} — is it running? {e})\n")
        last = c
    time.sleep(0.05)
