# hack_ras/version.py
"""
HEC-RAS program-version detection for HDF5 files.

The authoritative version of the program that wrote a HEC-RAS HDF file is the
root attribute ``File Version`` (e.g. ``"HEC-RAS 7.0 April 2026"``).  This is
reliable where other sources are not:

* The plan text file's ``Program Version=`` line is NOT updated consistently
  (a model re-run in 7.0 can still read ``Program Version=5.03``).
* The HDF root ``File Type`` attribute is also unreliable (a 7.0 geometry HDF
  has been observed labelled ``"HEC-RAS Results"``).

So version-sensitive HDF code should call :func:`RasVersion.from_hdf` and branch
on the parsed ``(major, minor)`` — never the text file, never ``File Type``.

The version is used for context in warnings/errors and, in the rare case where
the same dataset path means different things across versions, to disambiguate.
Layout selection itself is normally done by *structural probing* (see the
readers), which is inherently robust to new version numbers that keep an
existing schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import h5py

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


@dataclass(order=True)
class RasVersion:
    """
    A comparable HEC-RAS program version.

    Ordering is by ``(major, minor, patch)``; ``raw`` is excluded from
    comparison so ``RasVersion(7, 0) == RasVersion.parse("HEC-RAS 7.0 ...")``.

    Examples
    --------
    >>> RasVersion.parse("HEC-RAS 7.0 April 2026") >= RasVersion(6, 0)
    True
    """
    major: int
    minor: int
    patch: int = 0
    raw: str = field(default="", compare=False)

    def __str__(self) -> str:
        return self.raw or f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, text: str) -> "RasVersion":
        """
        Parse a version out of a ``File Version`` string such as
        ``"HEC-RAS 7.0 April 2026"`` or ``"HEC-RAS 5.0.3 September 2016"``.

        Raises
        ------
        ValueError
            If no ``<major>.<minor>`` number can be found in *text*.
        """
        m = _VERSION_RE.search(text or "")
        if not m:
            raise ValueError(f"No version number found in {text!r}")
        major, minor, patch = m.group(1), m.group(2), m.group(3)
        return cls(int(major), int(minor), int(patch) if patch else 0, raw=(text or "").strip())

    @classmethod
    def from_hdf(cls, hdf_path: str) -> Optional["RasVersion"]:
        """
        Read and parse the root ``File Version`` attribute of a HEC-RAS HDF file.

        Returns ``None`` if the attribute is absent or unparseable — callers
        that need it should treat ``None`` as "unknown version" rather than
        assuming a value.
        """
        with h5py.File(hdf_path, "r") as hdf:
            raw = hdf.attrs.get("File Version")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="replace")
        else:
            raw = str(raw)
        try:
            return cls.parse(raw)
        except ValueError:
            return None
