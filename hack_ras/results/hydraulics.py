# hack_ras/results/hydraulics.py
"""
Closed-conduit hydraulics that HEC-RAS does not store but RAS Mapper draws.

critical_depth reproduces the "Crit WS Pipe Max" line of RAS Mapper's pipe profile
plot. Checked on Hillside p05, 26th Ave trunk (2026-09-24): invert + critical
depth of Maximum Face Flow follows the plotted line over all 16 conduits by eye,
circular 1.25-6 ft. The tabular export could not confirm the numbers, because
it is buggy (see ai_context.md, "What the RAS Mapper pipe profile plot draws").
"""
from __future__ import annotations

import numpy as np

_G_US = 32.174      # ft/s^2
_G_SI = 9.80665     # m/s^2
_BISECT_STEPS = 60  # halves the bracket to ~1e-18 of the rise


def _circular_root(q2g, d):
    """Depth solving A^3/T = Q^2/g in a circle of diameter d, by bisection.

    A^3/T rises monotonically from 0 to infinity as the depth goes from 0 to d
    (the top width closes to zero at the crown), so a root always exists below
    the crown and bisection on (0, d) always converges to it.
    """
    lo = np.zeros_like(d)
    hi = d.copy()
    for _ in range(_BISECT_STEPS):
        y = 0.5 * (lo + hi)
        theta = 2.0 * np.arccos(np.clip(1.0 - 2.0 * y / d, -1.0, 1.0))
        area = d * d / 8.0 * (theta - np.sin(theta))
        top = d * np.sin(theta / 2.0)
        with np.errstate(divide='ignore', invalid='ignore'):
            f = np.where(top > 0, area ** 3 / top, np.inf)
        below = f < q2g
        lo = np.where(below, y, lo)
        hi = np.where(below, hi, y)
    return 0.5 * (lo + hi)


def critical_depth(flow, shape: str, rise, span=None,
                   si_units: bool = False) -> np.ndarray:
    """
    Critical depth for |flow| in a conduit section.

    Parameters
    ----------
    flow : array_like
        Discharge. The sign is ignored, so reverse flow gets the same depth.
    shape : str
        The conduit's ``Shape`` from Geometry/Pipe Conduits/Attributes.
        'Circular' and 'Box' are supported (every conduit on Hillside and the
        test fixture is one of the two).
    rise, span : array_like
        Section height and width. For 'Circular' the span is not used.
    si_units : bool
        Selects g.

    Returns
    -------
    np.ndarray, float64
        Depth above the invert. Zero where flow is zero. A box is capped at its
        rise, since the free-surface solution stops at the lid; a circle never
        needs the cap (see _circular_root).

    Raises
    ------
    ValueError
        For any other shape, rather than returning a number with no basis.
    """
    q = np.abs(np.asarray(flow, dtype=np.float64))
    rise = np.broadcast_to(np.asarray(rise, dtype=np.float64), q.shape)
    g = _G_SI if si_units else _G_US
    kind = str(shape).strip().lower()
    if kind == 'circular':
        yc = _circular_root(q * q / g, rise.astype(np.float64))
    elif kind == 'box':
        if span is None:
            raise ValueError("A 'Box' conduit needs its span")
        b = np.broadcast_to(np.asarray(span, dtype=np.float64), q.shape)
        yc = np.minimum((q * q / (g * b * b)) ** (1.0 / 3.0), rise)
    else:
        raise ValueError(
            f"critical_depth supports 'Circular' and 'Box' conduits, not "
            f"{shape!r}. Add the section's A and T before trusting a value.")
    return np.where(q > 0, yc, 0.0)
