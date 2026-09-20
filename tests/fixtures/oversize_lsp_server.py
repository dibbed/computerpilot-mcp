from __future__ import annotations

import sys
import time

while True:
    line = sys.stdin.buffer.readline()
    if line in {b"\r\n", b"\n", b""}:
        break

sys.stdout.buffer.write(b"Content-Length: 1000000\r\n\r\n")
sys.stdout.buffer.flush()
time.sleep(5)
