# hack_ras/geometry/blocks/xs_levee.py

from __future__ import annotations
from ..model import Levee


def _num(raw: str):
    raw = raw.strip()
    return None if raw == "" else float(raw)


def parse_levee(lines, index):
    """
    Parse a single-line ``Levee=`` block.  Returns ``(Levee, 1)``.

    File format (comma-separated, one line):
        Levee=<Lflag>,<Lsta>,<Lelev>,<Rflag>,<Rsta>,<Relev>,,<name>

    ``Lflag`` / ``Rflag`` are ``-1`` when that side's levee is present and ``0``
    (or blank) when it is absent.  When a side is absent its station/elevation
    fields are blank and stored as ``None``.

    Examples::

        Levee=-1,60,875,-1,200,874,,           # left & right levees
        Levee=-1,250,866.5,0,,,,                # left only
        Levee=0,,,-1,1500,866,,                 # right only
        Levee=0,,,-1,843.86,880.81,,Levee 1     # right only, named
    """
    body = lines[index].split("=", 1)[1].rstrip("\n")
    f = [p.strip() for p in body.split(",")]
    # pad to at least 8 fields so indexing is safe
    while len(f) < 8:
        f.append("")

    left_flag = int(f[0]) if f[0] else 0
    right_flag = int(f[3]) if f[3] else 0

    left_sta = _num(f[1]) if left_flag == -1 else None
    left_elev = _num(f[2]) if left_flag == -1 else None
    right_sta = _num(f[4]) if right_flag == -1 else None
    right_elev = _num(f[5]) if right_flag == -1 else None

    # The name is the trailing field(s); join in case a name contains commas.
    name = ",".join(f[7:]).strip()

    return Levee(
        left_sta=left_sta, left_elev=left_elev,
        right_sta=right_sta, right_elev=right_elev,
        name=name,
    ), 1
