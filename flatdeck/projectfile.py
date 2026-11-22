"""Save and open a project: a folder with the files that were loaded (copied), the jobs already generated and a JSON file with the rest."""
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import zipfile
from datetime import datetime

import numpy as np

from . import __version__
from .cam import CamResult
from .model import Layer, Project, Slot

FORMAT = 2                         # 1 had no jobs; it is still read
READABLE = (1, 2)
FILE = "flatdeck-project.json"
FILES_DIR = "files"
JOBS_DIR = "jobs"
_JOB_FILE = re.compile(r"^\d\d_.*\.(nc|npz)$")


class ProjectError(Exception):
    """A project cannot be saved or opened; the message is meant for the user."""


def is_project(folder: str) -> bool:
    return os.path.isfile(os.path.join(folder, FILE))


def _unique(name: str, used: set) -> str:
    base, ext = os.path.splitext(name)
    out, n = name, 1
    while out.lower() in used:
        n += 1
        out = f"{base}_{n}{ext}"
    used.add(out.lower())
    return out


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:60] or "job"


def _pts(a) -> list:
    return [[round(float(x), 6), round(float(y), 6)] for x, y in np.asarray(a, dtype=float).reshape(-1, 2)]


def _pack(items) -> tuple:
    """Polylines -> (all the points, start offsets): the form that goes into an .npz file."""
    arrs = [np.asarray(a, dtype=float).reshape(-1, 2) for a in items]
    starts = np.cumsum([0] + [len(a) for a in arrs]).astype(np.int64)
    return (np.vstack(arrs) if arrs else np.zeros((0, 2))), starts


def _unpack(pts, starts) -> list:
    pts, starts = np.asarray(pts, dtype=float), np.asarray(starts, dtype=np.int64)
    if pts.ndim != 2 or pts.shape[1] != 2 or starts.ndim != 1 or len(starts) < 1 or starts[0] != 0 or starts[-1] != len(pts) \
            or np.any(np.diff(starts) < 0):
        raise ValueError("inconsistent toolpath data")
    return [pts[starts[i]:starts[i + 1]].copy() for i in range(len(starts) - 1)]


def _write_job(folder: str, n: int, lay: Layer) -> dict:
    res = lay.job
    stem = f"{n:02d}_{_safe_name(lay.name)}"
    pts, starts = _pack([a for a, _ in res.paths])
    mpts, mstarts = _pack(res.marks)
    with open(os.path.join(folder, JOBS_DIR, stem + ".nc"), "w", encoding="utf-8") as f:
        f.write(res.gcode)                                     # also directly usable: it is the very file "Save G-code" writes
    np.savez_compressed(os.path.join(folder, JOBS_DIR, stem + ".npz"), pts=pts, starts=starts,
                        closed=np.array([bool(c) for _, c in res.paths], dtype=bool), marks=mpts, mstarts=mstarts,
                        holes=np.array(res.holes, dtype=float).reshape(-1, 3))
    return {"type": "job", "name": lay.name, "kind": res.kind, "file": f"{JOBS_DIR}/{stem}.nc", "data": f"{JOBS_DIR}/{stem}.npz",
            "visible": bool(lay.visible), "tool_dia": res.tool_dia, "length": res.length, "time_s": float(res.time_s),
            "origin": [float(res.origin[0]), float(res.origin[1])], "warnings": list(res.warnings)}


def save_project(project: Project, folder: str, ui: dict | None = None) -> str:
    """Write the project into `folder` (created if needed): the loaded files under files/, the generated jobs under jobs/ (the G-code
    as a plain .nc file plus the toolpaths for the preview) and the JSON file. Returns the path of the JSON file. The layers then
    point at their copies."""
    data_layers = project.data_layers()
    if not data_layers:
        raise ProjectError("There is nothing to save: load some files first")
    missing = [f"{l.name} ({l.path or 'no path'})" for l in data_layers if not l.path or not os.path.isfile(l.path)]
    if missing:
        raise ProjectError("These files are no longer on disk, so they cannot be copied into the project:\n" + "\n".join(missing))
    os.makedirs(os.path.join(folder, FILES_DIR), exist_ok=True)
    os.makedirs(os.path.join(folder, JOBS_DIR), exist_ok=True)
    used: set = set()
    entries, copies = [], []
    for l in project.layers:                                   # one list, in the order of the layer list (jobs sit among the files)
        if l.kind == "job":
            entries.append(l)                                  # written below, once the copies went well
            continue
        dest = _unique(os.path.basename(l.path), used)
        target = os.path.join(folder, FILES_DIR, dest)
        entries.append({
            "type": "layer", "file": f"{FILES_DIR}/{dest}", "name": l.name, "kind": l.kind, "side": l.side, "visible": bool(l.visible),
            "hv_points": _pts(l.hv_points) if l.hv_points else [], "ops": [[op, cx, cy] for op, cx, cy in l.ops],
        })
        copies.append((l, l.path, target))
    for l, src, target in copies:                              # copy first: nothing is written if a copy fails
        same = os.path.exists(target) and (os.path.samefile(src, target) or filecmp.cmp(src, target, shallow=False))
        if not same:
            try:
                shutil.copyfile(src, target)
            except OSError as e:
                raise ProjectError(f"Cannot copy {l.name} into the project: {e}") from e
    try:
        for name in os.listdir(os.path.join(folder, JOBS_DIR)):            # jobs removed since the last save must not linger
            if _JOB_FILE.match(name):
                os.remove(os.path.join(folder, JOBS_DIR, name))
        n = 0
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                n += 1
                entries[i] = _write_job(folder, n, e)
    except OSError as e:
        raise ProjectError(f"Cannot write the generated jobs: {e}") from e
    data = {
        "format": FORMAT, "app_version": __version__, "saved": datetime.now().isoformat(timespec="seconds"),
        "origin_mode": project.origin_mode, "layers": entries,
        "slots": [{"pts": _pts(s.pts), "auto": bool(s.auto)} for s in project.slots],
        "ui": ui or {},
    }
    path = os.path.join(folder, FILE)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        raise ProjectError(f"Cannot write the project file: {e}") from e
    for l, _, target in copies:
        l.path = target                                        # from now on the project works on its own copies
    return path


def _safe_path(folder: str, rel: str) -> str:
    """The file inside `folder` that `rel` names; refuses anything that would leave it (a project folder is untrusted data)."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        raise ProjectError(f"The project names a file outside its folder: {rel!r}")
    full = os.path.realpath(os.path.join(folder, rel))
    if os.path.commonpath([full, os.path.realpath(folder)]) != os.path.realpath(folder):
        raise ProjectError(f"The project names a file outside its folder: {rel!r}")
    return full


def _read_job(folder: str, e: dict) -> Layer:
    gpath, dpath = _safe_path(folder, e.get("file")), _safe_path(folder, e.get("data"))
    for p in (gpath, dpath):
        if not os.path.isfile(p):
            raise ProjectError(f"The project file {os.path.relpath(p, os.path.realpath(folder))} is missing from the project folder")
    with open(gpath, "r", encoding="utf-8") as f:
        gcode = f.read()
    try:
        with np.load(dpath, allow_pickle=False) as z:                  # no pickle: an .npz from a project folder is data, not code
            paths = _unpack(z["pts"], z["starts"])
            closed = np.asarray(z["closed"], dtype=bool)
            marks = _unpack(z["marks"], z["mstarts"])
            holes = np.asarray(z["holes"], dtype=float).reshape(-1, 3)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as ex:
        raise ProjectError(f"The toolpath data of {e.get('name', 'a job')} is damaged: {ex}") from ex
    if len(closed) != len(paths):
        raise ProjectError(f"The toolpath data of {e.get('name', 'a job')} is damaged: inconsistent toolpath data")
    org = e.get("origin", [0.0, 0.0])
    res = CamResult(kind=str(e.get("kind", "")), name=str(e.get("name", "")), gcode=gcode,
                    paths=[(a, bool(c)) for a, c in zip(paths, closed)], holes=[tuple(map(float, h)) for h in holes], marks=marks,
                    origin=(float(org[0]), float(org[1])), tool_dia=float(e.get("tool_dia", 0.0)),
                    warnings=[str(w) for w in e.get("warnings", [])], length=float(e.get("length", 0.0)), time_s=float(e.get("time_s", 0.0)))
    return Layer(res.name, "job", job=res, visible=bool(e.get("visible", True)))


def load_project(folder: str):
    """(Project, ui dict, warnings). Raises ProjectError with a readable message."""
    path = os.path.join(folder, FILE)
    if not os.path.isfile(path):
        raise ProjectError(f"{folder} is not a FlatDeck project (there is no {FILE})")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ProjectError(f"Cannot read the project file: {e}") from e
    if not isinstance(data, dict) or data.get("format") not in READABLE:
        raise ProjectError(f"The project was saved by a different version of FlatDeck (format {data.get('format') if isinstance(data, dict) else '?'}): "
                           f"this one reads formats {', '.join(map(str, READABLE))}")
    project, warnings = Project(), []
    project.origin_mode = data.get("origin_mode", "board") if data.get("origin_mode") in ("board", "file") else "board"
    try:
        for e in data.get("layers", []):
            if e.get("type") == "job":
                project.layers.append(_read_job(folder, e))
                continue
            full = _safe_path(folder, e.get("file"))
            if not os.path.isfile(full):
                raise ProjectError(f"The project file {e.get('file')} is missing from the project folder")
            lay = project.load(full, kind="drill" if e.get("kind") == "drill" else "gerber")
            if e.get("kind") in ("copper", "outline", "silk", "mask", "drill"):
                lay.kind = e["kind"]
            lay.name = e.get("name") or lay.name
            lay.side = e.get("side", lay.side)
            lay.visible = bool(e.get("visible", True))
            project.replay(lay, e.get("ops", []))
            lay.hv_points = [(float(x), float(y)) for x, y in e.get("hv_points", [])]
            for w in lay.warnings:
                warnings.append(f"{lay.name}: {w}")
        for s in data.get("slots", []):
            pts = np.array(s["pts"], dtype=float)
            if pts.ndim == 2 and len(pts) >= 2:
                project.slots.append(Slot(pts, bool(s.get("auto", False))))
    except ProjectError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, OSError) as e:
        raise ProjectError(f"The project file is damaged: {e}") from e
    return project, (data.get("ui") if isinstance(data.get("ui"), dict) else {}), warnings
