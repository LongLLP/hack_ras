# hack_ras/project/compute.py
"""Run plans headlessly with HEC-RAS 7.0's command line: ``Ras.exe -c``.

    from hack_ras import RasProject, compute
    report = compute.run_plans(project, "5-24", ras_version="7.0")
    print(compute.format_report(report))

One ``Ras.exe -c "<prj>" "<plan>" -hideCompute`` per plan, in order, each
waited on. Measured on scratch copies 2026-10-08 (TODO.md §H, step 0):

* ``-c`` blocks until the run ends, then RAS closes itself. It computes the
  plan NAMED on the command line and leaves ``Current Plan=`` in the .prj alone.
  What it writes matches a GUI run: the plan family, the geometry's
  ``.g##.hdf`` / ``.x##``, the flow's ``.u##.hdf``, the shared ``.dss``.
* ``-c`` does NOT raise the "already an instance of HEC-RAS running" prompt,
  so it is safe while the user has RAS open (same or other version). A bare
  launch or ``-h`` DOES raise it — never launch Ras.exe any other way here.
* **The exit code is 0 even when the run fails.** Success is therefore read
  from the plan HDF: ``Solution`` must say "Finished Successfully", and the
  file must have been written after launch. A failed run writes
  ``Unsteady failed to run`` and no ``Run Time Window``.
* **Most failures end the run by themselves; some stop on a modal dialog.** A
  Fortran crash (``forrtl:``) shows in the compute window, which closes itself.
  But ``Ras.exe`` (a VB6 program) can stop on a message box that waits for a
  click forever — "Run-time error 53: File not found" for a too-long path, and
  a hang after RAS could not delete its own ``.tmp.hdf`` on the X: drive.
  So while a plan runs, standard dialog boxes (class ``#32770``) owned by the
  ``Ras.exe`` we launched or its children are watched; one that stays up
  ``_DIALOG_GRACE`` seconds is read, the run is killed, and the status is
  ``dialog``. RAS's own forms (compute window, geometry "Load Messages") are
  VB forms, not ``#32770``, and never trigger it — a healthy run of any length
  is left alone. ``timeout`` remains an optional wall-clock backstop.
* The COM controller (``RAS70.HECRASController``) is deliberately NOT used: it
  attached to the user's open GUI, loaded the project into it, and
  ``QuitRas`` closed it. Its compute return value was also wrong.

Version guard. Running a plan in a different RAS version than last ran it is
refused unless ``force_version=True``: a 7.0.1 run of a 7.0 plan rewrote the
SHARED ``.g##.hdf`` (and stamped it 7.0.1), and 7.0.1 refuses a 7.0 restart
file outright. The version is read from each HDF's root ``File Version`` —
never the text files' ``Program Version=``, which stayed ``7.00`` throughout.
7.0 and 7.0.1 are different versions; the caller names the exact one.

Restart order. A plan whose unsteady flow file restarts from
``Base.p##.<stamp>.rst`` (``Use Restart=-1``) depends on plan p##. If both are
in the batch the producer must run first: a bad order raises
``RestartOrderError`` unless ``reorder=True``, which moves producers ahead of
their consumers and otherwise keeps the given order. If a producer fails, its
consumers are skipped; everything else keeps going.

RAS's geometry "Load Messages" window (e.g. "XS Htab Starting Elevations ...
reset to defaults") appears during ``-c`` but does not block it, and its text
is NOT written to the HDF; this module does not capture it.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time

from hack_ras.project.plans import (
    PlanFileNotFound,
    _assert_no_active_run,
    plan_path,
    plans_with_unlisted_results,
    read_plan_sidecar,
)
from hack_ras.project.ras_project import RasProject
from hack_ras.resolve import expand_id_spec
from hack_ras.utils.lines import content_of, read_lines

logger = logging.getLogger(__name__)

RAS_ROOT = r"C:\Program Files (x86)\HEC\HEC-RAS"

_SOLUTION_GROUPS = ("Results/Unsteady/Summary", "Results/Steady/Summary",
                    "Results/Summary")
_MESSAGES = "Results/Summary/Compute Messages (text)"
_TAIL_LINES = 40
# Longest path RAS 7.0 can write. Measured 2026-10-08: with the plan's
# `.p##.tmp.hdf` at 260 characters Ras.exe -c stopped on a modal
# "Run-time error 53: File not found" (a VB error in Ras.exe, before the
# solver starts) and hung until killed. Only the .tmp.hdf is checked —
# minimal by design; a restart file written by the plan is 11 characters
# longer still, so a folder within ~11 of the limit can still fail.
_MAX_PATH = 259
# A dialog box owned by our Ras.exe that stays up this long is blocking the
# run (nobody will click it): read it, kill the run.
_DIALOG_GRACE = 3.0


class RasVersionMismatch(RuntimeError):
    """A plan (or its geometry / restart source) was last run in another
    HEC-RAS version than the one requested."""


class RestartOrderError(ValueError):
    """A plan would run before the plan whose restart file it consumes."""


# ---------------------------
# Executable / version
# ---------------------------

def installed_versions(root: str = RAS_ROOT) -> list[str]:
    """Version folders under the HEC-RAS install root that hold a Ras.exe."""
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if os.path.isfile(os.path.join(root, d, "Ras.exe")))


def _check_supported(ras_version: str) -> None:
    """Running plans is HEC-RAS 7.0 and later only (user decision 2026-10-08).
    Older versions are out of scope: 4.1's ras.exe has no ``-c`` batch mode
    (it would open the GUI and wait), and their route — the COM controller —
    is not used here at all."""
    from hack_ras.version import RasVersion
    if RasVersion.parse(str(ras_version)) < RasVersion(7, 0):
        raise ValueError(
            f"Running plans needs HEC-RAS 7.0 or later, not {ras_version!r}; "
            "older versions are out of scope.")


def ras_exe_path(ras_version: str, root: str = RAS_ROOT) -> str:
    """``<root>\\<ras_version>\\Ras.exe``, matched EXACTLY — '7.0' never means
    '7.0.1'. Raises ValueError below 7.0 and FileNotFoundError naming the
    installed versions."""
    _check_supported(ras_version)
    exe = os.path.join(root, str(ras_version), "Ras.exe")
    if not os.path.isfile(exe):
        raise FileNotFoundError(
            f"No Ras.exe for HEC-RAS {ras_version!r} under {root}. "
            f"Installed: {installed_versions(root) or 'none'}")
    return exe


def _hdf_version(path: str):
    """RasVersion of an HDF's root 'File Version', or None if absent."""
    if not os.path.isfile(path):
        return None
    from hack_ras.version import RasVersion   # lazy: imports h5py
    try:
        return RasVersion.from_hdf(path)
    except OSError:
        return None


# ---------------------------
# Selection / dependencies
# ---------------------------

def _ordered_ids(spec) -> list[str]:
    """Like expand_id_spec but keeps the caller's ORDER (a range still
    expands ascending); duplicates keep their first position."""
    if isinstance(spec, (str, int)):
        spec = [spec]
    out = []
    for entry in spec:
        for token in str(entry).split(","):
            if token.strip():
                for pid in expand_id_spec(token.strip(), kind="p"):
                    if pid not in out:
                        out.append(pid)
    return out


def restart_source(project: RasProject, plan_id: str) -> str | None:
    """Plan id whose restart file this plan's unsteady flow starts from, or
    None. Only an ACTIVE restart counts (``Use Restart=-1``); RAS keeps the
    filename stored when the option is off. A restart name carrying no plan
    number (e.g. ``banana.rst``) has no plan source."""
    flow_id = read_plan_sidecar(plan_path(project, plan_id))["flow_id"]
    if not flow_id or not flow_id.lower().startswith("u"):
        return None
    upath = os.path.join(project.folder, f"{project.base_name}.{flow_id}")
    if not os.path.isfile(upath):
        return None
    use, name = False, ""
    for line in read_lines(upath):
        c = content_of(line)
        if c.startswith("Use Restart="):
            use = c[len("Use Restart="):].strip() == "-1"
        elif c.startswith("Restart Filename="):
            name = c[len("Restart Filename="):].strip()
    prefix = f"{project.base_name}.p".lower()
    if not use or not name.lower().startswith(prefix):
        return None
    num = name[len(prefix):].split(".", 1)[0]
    if not (len(num) == 2 and num.isdigit()):
        return None
    return f"p{num}"


def _order_by_restarts(order: list[str], sources: dict, reorder: bool) -> list[str]:
    """Check (or, with reorder, fix) that every in-batch restart producer runs
    before its consumer. The fix is a stable topological sort: at each step
    take the earliest plan in the given order whose producer is already placed."""
    pos = {pid: i for i, pid in enumerate(order)}
    bad = [(pid, src) for pid, src in sources.items()
           if src in pos and pos[src] > pos[pid]]
    if not bad:
        return order
    if not reorder:
        lines = "; ".join(f"{src} must run before {pid} (it restarts from "
                          f"{src}'s .rst)" for pid, src in bad)
        raise RestartOrderError(
            f"Restart order: {lines}. Reorder the spec, or pass reorder=True.")
    placed, remaining = [], list(order)
    while remaining:
        for pid in remaining:
            src = sources.get(pid)
            if src not in pos or src in placed:
                placed.append(pid)
                remaining.remove(pid)
                break
        else:
            raise RestartOrderError(f"Restart cycle among {remaining}")
    logger.info("restart order: reordered %s -> %s", order, placed)
    return placed


# ---------------------------
# Pre-flight
# ---------------------------

def prepare_runs(project: RasProject, spec, ras_version: str,
                 force_version: bool = False, reorder: bool = False) -> dict:
    """Validate a batch without running anything. Returns
    ``{'order', 'restart_sources', 'version_mismatches'}``.

    Raises before any run on: an unlisted or missing plan, a plan mid-run,
    a missing geometry or flow file, a bad restart order (unless reorder),
    and a version mismatch (unless force_version — then it is only reported).
    A plan that has never run (no HDF) imposes no version.
    """
    from hack_ras.version import RasVersion
    _check_supported(ras_version)
    target = RasVersion.parse(str(ras_version))
    order = _ordered_ids(spec)
    if not order:
        raise ValueError("No plans selected.")
    listed = set(project.model.plan_file_ids)
    for pid in order:
        if pid not in listed:
            raise PlanFileNotFound(f"{pid} is not listed in {project.base_name}.prj")
        ppath = plan_path(project, pid)
        if not os.path.isfile(ppath):
            raise PlanFileNotFound(f"Plan file missing: {ppath}")
        _assert_no_active_run(project, pid)
        tmp = os.path.abspath(ppath) + ".tmp.hdf"
        if len(tmp) > _MAX_PATH:
            raise ValueError(
                f"{pid}: RAS would write {len(tmp)}-character path {tmp} "
                f"(Windows limit {_MAX_PATH}); Ras.exe stops on a modal "
                "'Run-time error 53: File not found'. Move the model to a shorter path.")
        side = read_plan_sidecar(ppath)
        for kind, fid in (("geometry", side["geom_id"]), ("flow", side["flow_id"])):
            if not fid:
                raise FileNotFoundError(f"{pid} names no {kind} file")
            fpath = os.path.join(project.folder, f"{project.base_name}.{fid}")
            if not os.path.isfile(fpath):
                raise FileNotFoundError(f"{pid}: {kind} file missing: {fpath}")

    sources = {}
    for pid in order:
        src = restart_source(project, pid)
        if src:
            sources[pid] = src
    order = _order_by_restarts(order, sources, reorder)

    mismatches = []
    for pid in order:
        side = read_plan_sidecar(plan_path(project, pid))
        checks = [(f"{pid}.hdf", plan_path(project, pid) + ".hdf"),
                  (f"{side['geom_id']}.hdf", os.path.join(
                      project.folder, f"{project.base_name}.{side['geom_id']}.hdf"))]
        src = sources.get(pid)
        if src and src not in order:   # an in-batch producer is rewritten first
            checks.append((f"{src}.hdf (restart source)",
                           plan_path(project, src) + ".hdf"))
        for label, path in checks:
            v = _hdf_version(path)
            if v is not None and v != target:
                mismatches.append(f"{pid}: {label} is {v}, not {ras_version}")
    if mismatches and not force_version:
        raise RasVersionMismatch(
            "Refusing to run in HEC-RAS " + str(ras_version) + ": "
            + "; ".join(mismatches)
            + ". Running in another version rewrites shared geometry HDFs; "
              "pass force_version=True if that is intended.")
    for m in mismatches:
        logger.warning("version mismatch (forced): %s", m)
    return {"order": order, "restart_sources": sources,
            "version_mismatches": mismatches}


# ---------------------------
# Results check
# ---------------------------

def read_run_status(hdf_path: str) -> dict:
    """``{'solution', 'run_time_window', 'messages'}`` from a plan HDF; each
    None when absent. ``messages`` is RAS's Compute Messages (text)."""
    import h5py   # lazy

    def s(v):
        if v is None:
            return None
        if hasattr(v, "tobytes"):
            v = v.tobytes()
        return v.decode("latin-1").rstrip("\x00") if isinstance(v, bytes) else str(v)

    out = {"solution": None, "run_time_window": None, "messages": None}
    with h5py.File(hdf_path, "r") as h:
        for g in _SOLUTION_GROUPS:
            if g in h and "Solution" in h[g].attrs:
                out["solution"] = s(h[g].attrs["Solution"])
                out["run_time_window"] = s(h[g].attrs.get("Run Time Window"))
                break
        if _MESSAGES in h:
            out["messages"] = s(h[_MESSAGES][()])
    return out


def _failure_line(text: str | None) -> str:
    """One line saying why a run failed, from its compute messages: a Fortran
    runtime error first ('forrtl: severe (159): ...' — measured with the .dss
    locked: RasUnsteady.exe crashed in DSS_OPEN, Ras.exe exited by itself,
    and the HDF held ONLY Results/Summary, no Solution and no results), else
    RAS's own LAST error line — its conclusion. Earlier error lines are often
    library noise: Hillside p05's geometry-write failure opened with a page of
    'HDF5-DIAG: Error detected ...' and ended 'Error Processing Geometry'."""
    lines = [l.strip() for l in (text or "").replace("\r", "").split("\n")]
    for l in lines:
        if "forrtl:" in l.lower():
            return l
    for l in reversed(lines):
        if "error" in l.lower() and not l.startswith("HDF5-DIAG"):
            return l
    return ""


def _tail(text: str | None, n: int = _TAIL_LINES) -> str:
    if not text:
        return ""
    lines = [l for l in text.replace("\r", "").split("\n")]
    return "\n".join(lines[-n:]).strip()


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def _child_pids(parent: int) -> set[int]:
    """Live processes whose parent is ``parent`` (Windows Toolhelp snapshot).
    The parent id survives the parent's exit, so this finds orphans too."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return set()
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    kids = set()
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent:
                kids.add(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return kids


def _wait_for_children(parent: int, limit: float = 30.0) -> None:
    """Ras.exe starts RasPlotDriver.exe, which outlives it by about a second
    and keeps the project folder busy (measured 2026-10-08). Wait for the
    children of the Ras.exe we launched to exit before touching files."""
    end = time.time() + limit
    while _child_pids(parent) and time.time() < end:
        time.sleep(0.25)
    left = _child_pids(parent)
    if left:
        logger.warning("Ras.exe child processes still running after %.0f s: %s",
                       limit, sorted(left))


def _process_tree(root: int) -> set[int]:
    """root and every live descendant."""
    tree, todo = {root}, [root]
    while todo:
        for kid in _child_pids(todo.pop()):
            if kid not in tree:
                tree.add(kid)
                todo.append(kid)
    return tree


def _dialogs_of(pids: set[int]) -> dict[int, str]:
    """Visible standard dialog boxes (class #32770 — what MsgBox and VB's
    run-time-error box create) owned by any of pids, as {hwnd: text}. Text is
    the title plus every control's text, read with WM_GETTEXT, which works
    across processes. Reads only; never clicks."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def text(h):
        n = u32.SendMessageW(h, 0x000E, 0, 0)                 # WM_GETTEXTLENGTH
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.SendMessageW(h, 0x000D, n + 1, buf)               # WM_GETTEXT
        return buf.value.strip()

    found = {}

    def on_top(h, _):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(h, cls, 64)
            if cls.value == "#32770":
                parts = [text(h)]

                def on_child(c, _):
                    parts.append(text(c))
                    return True
                u32.EnumChildWindows(h, enum_proc(on_child), 0)
                found[h] = " | ".join(" ".join(p.split()) for p in parts if p)
        return True

    u32.EnumWindows(enum_proc(on_top), 0)
    return found


def _wait_watching(proc, timeout, grace: float, poll: float = 1.0):
    """Wait for proc. Returns (None, "") when it exits by itself,
    ("timeout", "") past timeout, or ("dialog", text) when a dialog box owned
    by its process tree has stayed up `grace` seconds. Kills nothing."""
    t0 = time.time()
    first_seen = {}
    while True:
        try:
            proc.wait(timeout=poll)
            return None, ""
        except subprocess.TimeoutExpired:
            pass
        now = time.time()
        if timeout is not None and now - t0 >= timeout:
            return "timeout", ""
        if grace is None:
            continue
        dialogs = _dialogs_of(_process_tree(proc.pid))
        first_seen = {h: first_seen.get(h, now) for h in dialogs}
        for h, seen in first_seen.items():
            if now - seen >= grace:
                return "dialog", dialogs[h]


# ---------------------------
# Run
# ---------------------------

def run_plans(project: RasProject, spec, ras_version: str,
              force_version: bool = False, reorder: bool = False,
              timeout: float | None = None, hide_compute: bool = True,
              kill_on_dialog: bool = True, ras_root: str = RAS_ROOT) -> dict:
    """Compute plans one at a time with ``Ras.exe -c``; keep going past a failure.

    ``spec`` is a plan id-spec whose ORDER is the run order ('p05,p04',
    ['5-7', 2]); a range runs ascending. ``ras_version`` is the exact install
    folder ('7.0', '7.0.1') and is required — there is no default version.
    ``kill_on_dialog`` (default on) ends a run that stops on a modal dialog
    box — status 'dialog', the box's text as the reason — and never touches a
    healthy run however long it takes. ``timeout`` (seconds, per plan; None =
    no limit) is an optional wall-clock backstop. Both kill only the process
    tree this function started.

    Returns ``{'ras_exe', 'ras_version', 'order', 'version_mismatches',
    'runs', 'unlisted_results'}``. Each run is ``{'plan', 'title', 'status',
    'solution', 'run_time_window', 'seconds', 'reason', 'messages_tail',
    'data_errors'}``,
    status one of 'ok' | 'failed' | 'no_results' | 'dialog' | 'timeout' |
    'skipped'.
    Nothing here edits the .prj or the .rasmap; ``unlisted_results`` lists
    the plans RAS Mapper will append to its Results tree on next open.
    """
    exe = ras_exe_path(ras_version, ras_root)
    prep = prepare_runs(project, spec, ras_version, force_version, reorder)
    prj = os.path.join(project.folder, f"{project.base_name}.prj")
    failed, runs = set(), []
    for pid in prep["order"]:
        ppath = plan_path(project, pid)
        run = {"plan": pid, "title": read_plan_sidecar(ppath)["title"],
               "status": None, "solution": None, "run_time_window": None,
               "seconds": None, "reason": "", "messages_tail": "",
               "data_errors": ""}
        runs.append(run)
        src = prep["restart_sources"].get(pid)
        if src in failed:
            run["status"] = "skipped"
            run["reason"] = f"restarts from {src}, which did not finish"
            failed.add(pid)
            logger.warning("%s skipped: %s", pid, run["reason"])
            continue

        # RAS wants file arguments quoted, so the command is one string.
        cmd = f'"{exe}" -c "{prj}" "{ppath}"' + (" -hideCompute" if hide_compute else "")
        logger.info("running %s (%s) in HEC-RAS %s", pid, run["title"], ras_version)
        t0 = time.time()
        proc = subprocess.Popen(cmd)
        stop, dialog = _wait_watching(proc, timeout,
                                      _DIALOG_GRACE if kill_on_dialog else None)
        if stop:
            _kill_tree(proc.pid)
            proc.wait()
            run["status"] = stop
        run["seconds"] = round(time.time() - t0, 1)
        _wait_for_children(proc.pid)
        if stop == "timeout":
            run["reason"] = (f"killed after {timeout} s - RAS may have been "
                             "waiting on a dialog")
        elif stop == "dialog":
            run["reason"] = f"RAS stopped on a dialog, killed: {dialog}"

        hdf = ppath + ".hdf"
        if run["status"] is None:
            # 1 s slack: the HDF is renamed into place, and file times are coarse.
            if not os.path.isfile(hdf) or os.path.getmtime(hdf) < t0 - 1:
                run["status"] = "no_results"
                run["reason"] = "Ras.exe exited without writing a new plan HDF"
            else:
                st = read_run_status(hdf)
                run["solution"] = st["solution"]
                run["run_time_window"] = st["run_time_window"]
                ok = bool(st["solution"]) and "finished successfully" in st["solution"].lower()
                run["status"] = "ok" if ok else "failed"
                if not ok:
                    run["messages_tail"] = _tail(st["messages"])
                    run["reason"] = _failure_line(st["messages"]) or (
                        "" if st["solution"] else "no Solution recorded in the plan HDF")
        # RAS writes <plan>.data_errors.txt for some failures and NOT into the
        # HDF — the X: drive hang (Hillside p05, 2026-10-08) left only "Unable to
        # delete temporary results file ... File not found" there. A stale copy
        # from an earlier run is ignored.
        derr = ppath + ".data_errors.txt"
        if os.path.isfile(derr) and os.path.getmtime(derr) >= t0 - 1:
            with open(derr, encoding="latin-1") as f:
                run["data_errors"] = f.read().strip()
        if run["status"] != "ok":
            failed.add(pid)
            # A crash or kill can leave the never-finalized results behind
            # (Solution 'Running Unsteady'); hack_ras then reads the plan as
            # mid-run (PlanRunActive) until it is removed.
            if os.path.isfile(ppath + ".tmp.hdf"):
                run["reason"] = "; ".join(x for x in (run["reason"], (
                    f"RAS left {project.base_name}.{pid}.tmp.hdf (unfinalized "
                    "results) - delete it before re-running")) if x)
            logger.warning("%s %s %s", pid, run["status"],
                           " - ".join(x for x in (run["solution"], run["reason"]) if x))
        else:
            logger.info("%s ok in %.1f s", pid, run["seconds"])

    return {"ras_exe": exe, "ras_version": str(ras_version),
            "order": prep["order"],
            "version_mismatches": prep["version_mismatches"],
            "runs": runs,
            "unlisted_results": plans_with_unlisted_results(project)}


def format_report(report: dict) -> str:
    """Readable summary of a run_plans report."""
    out = [f"HEC-RAS {report['ras_version']}  ({report['ras_exe']})"]
    for m in report["version_mismatches"]:
        out.append(f"  FORCED version mismatch: {m}")
    for r in report["runs"]:
        secs = f"{r['seconds']:.1f} s" if r["seconds"] is not None else "-"
        detail = " - ".join(x for x in (r["solution"], r["reason"]) if x)
        out.append(f"  {r['plan']}  {r['status']:<10} {secs:>8}  {r['title']}  | {detail}")
        if r.get("data_errors"):
            out.append(f"      {r['plan']}.data_errors.txt:")
            out.extend("        " + l for l in r["data_errors"].splitlines())
        if r["messages_tail"]:
            out.extend("      " + l for l in r["messages_tail"].splitlines())
    n_ok = sum(r["status"] == "ok" for r in report["runs"])
    out.append(f"{n_ok} of {len(report['runs'])} ok")
    if report["unlisted_results"]:
        out.append("RAS Mapper will append Results layers for: "
                   + ", ".join(report["unlisted_results"])
                   + " (open it once, then project.rasmap.sort())")
    return "\n".join(out)
