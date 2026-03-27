#!/usr/bin/env python3
"""
Bell Flag Spike — run ONCE manually to determine tmux bell flag read behavior.

Usage:
    python3 coordinator/spike_bell_flag.py

What this tests:
    Does `tmux display-message -p "#{window_bell_flag}"` clear the flag when
    read, or does the flag persist until the window is visited in tmux?

Expected result (almost certain): the flag persists. Reading it does NOT clear
it. The flag is cleared only when the window is marked active inside tmux.
"""

import subprocess
import time

SESSION = "bell-spike-test"


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def main() -> None:
    # 1. Create a test session
    print("Creating test session...")
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", SESSION, "-x", "80", "-y", "24"], check=True
    )
    time.sleep(0.2)

    # 2. Send a bell to the session
    print("Sending bell to session...")
    subprocess.run(
        ["tmux", "send-keys", "-t", SESSION, "printf '\\a'", "Enter"], check=True
    )
    time.sleep(1.0)

    # 3. Read the bell flag (first read)
    flag_read1 = run(
        ["tmux", "display-message", "-t", SESSION, "-p", "#{window_bell_flag}"]
    )
    print(f"Bell flag (1st read): '{flag_read1}'")

    # 4. Read the bell flag immediately again (second read)
    flag_read2 = run(
        ["tmux", "display-message", "-t", SESSION, "-p", "#{window_bell_flag}"]
    )
    print(f"Bell flag (2nd read): '{flag_read2}'")

    # 5. Cleanup
    subprocess.run(["tmux", "kill-session", "-t", SESSION])

    # 6. Report
    print()
    if flag_read1 == "1" and flag_read2 == "1":
        print("FINDING: Reading does NOT clear the flag. Both reads show '1'.")
        print(
            "Implementation: use in-memory _bell_seen dict to detect 0→1 transitions."
        )
    elif flag_read1 == "1" and flag_read2 == "0":
        print(
            "FINDING: Reading CLEARS the flag. First read shows '1', second shows '0'."
        )
        print("Implementation: each '1' is a new bell — no transition tracking needed.")
    elif flag_read1 == "0":
        print("WARNING: Bell flag not set after printf '\\a'. Try running manually:")
        print(f"  tmux send-keys -t {SESSION} \"printf '\\\\a'\" Enter")
        print(
            "  Then check: tmux display-message -t bell-spike-test -p '#{window_bell_flag}'"
        )
    else:
        print(f"UNEXPECTED: read1={flag_read1!r}, read2={flag_read2!r}")


if __name__ == "__main__":
    main()
