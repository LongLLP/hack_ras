# hack_ras/utils/lines.py
"""Raw-line I/O for HEC-RAS text files — lossless by construction.

Files are read as raw lines with their endings attached and written back by
plain concatenation, so every untouched line round-trips byte-for-byte
(CRLF preserved, trailing whitespace preserved, no final-newline surprises).
latin-1 maps every byte to a code point, so arbitrary bytes survive the trip.
"""
from __future__ import annotations


def read_lines(path: str) -> list[str]:
    """Read a RAS text file as raw lines with endings attached ('...\\r\\n')."""
    with open(path, "r", encoding="latin-1", newline="") as f:
        text = f.read()
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def write_lines(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write("".join(lines))


def eol_of(lines: list[str]) -> str:
    """Line ending used by the file ('\\r\\n' or '\\n')."""
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\r\n"


def content_of(line: str) -> str:
    """The line without its ending."""
    return line.rstrip("\r\n")
