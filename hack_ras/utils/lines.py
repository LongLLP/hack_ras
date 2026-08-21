# hack_ras/utils/lines.py
"""Raw-line I/O for HEC-RAS text files — lossless by construction.

Files are read as raw lines with their endings attached and written back by
plain concatenation, so every untouched line round-trips byte-for-byte
(CRLF preserved, trailing whitespace preserved, no final-newline surprises).
latin-1 maps every byte to a code point, so arbitrary bytes survive the trip.

**UTF-8 BOM handling.** Some Windows editors prepend a UTF-8 BOM (bytes
EF BB BF) when saving a RAS text file; HEC-RAS ignores it and runs such files
fine (observed live: nine BOM'd plans with results on disk). Under latin-1
those three bytes decode to three ordinary characters sitting in front of the
first key, which makes every line-1 key match fail — a BOM'd `.p##` reports an
empty `Plan Title=`, which in turn blinds the duplicate-title guards in
`clone_plan` / `clone_geom` / `clone_flow` and makes `project_health` print
"(missing)" for a title that is really there.

`read_lines` therefore strips a leading BOM so callers never have to think
about it, and `write_lines` re-attaches one if the destination file already
had it. Together that keeps an in-place edit byte-for-byte identical (the
14-of-17 write sites that rewrite the file they just read) while the lines
list stays BOM-free, so no `startswith` or field-width calculation has to be
BOM-aware. Files created fresh (the three clone sites) are simply BOM-free.

Note the resulting coupling: `write_lines` preserves the BOM by inspecting
the destination, which is correct only because no caller overwrites an
unrelated file — the clone functions all verify the target ID is free (both
in the `.prj` and on disk) before writing. A future "write to temp, then
rename" refactor for atomicity would silently defeat BOM preservation,
because the temp path has no BOM to find; `tests/test_bom_handling.py`
guards that.
"""
from __future__ import annotations

_UTF8_BOM_BYTES = b"\xef\xbb\xbf"
# The same three bytes as latin-1 decodes them, i.e. what read() hands back.
_UTF8_BOM = _UTF8_BOM_BYTES.decode("latin-1")


def _has_bom(path: str) -> bool:
    """True if the file exists and starts with a UTF-8 BOM."""
    try:
        with open(path, "rb") as f:
            return f.read(3) == _UTF8_BOM_BYTES
    except FileNotFoundError:
        return False


def read_lines(path: str) -> list[str]:
    """Read a RAS text file as raw lines with endings attached ('...\r\n').

    A leading UTF-8 BOM is stripped (see module docstring); `write_lines`
    puts it back when rewriting the same file.
    """
    with open(path, "r", encoding="latin-1", newline="") as f:
        text = f.read()
    if text.startswith(_UTF8_BOM):
        text = text[len(_UTF8_BOM):]
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def write_lines(path: str, lines: list[str]) -> None:
    """Write raw lines back by plain concatenation.

    A UTF-8 BOM already on the destination file is preserved, so rewriting a
    file that `read_lines` just read is byte-for-byte identical. A file being
    created fresh gets no BOM.
    """
    prefix = _UTF8_BOM if _has_bom(path) else ""
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(prefix + "".join(lines))


def eol_of(lines: list[str]) -> str:
    """Line ending used by the file ('\r\n' or '\n')."""
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\r\n"


def content_of(line: str) -> str:
    """The line without its ending."""
    return line.rstrip("\r\n")
