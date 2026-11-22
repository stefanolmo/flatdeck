"""Save and open a project: a folder with the files that were loaded (copied) and a JSON file with everything else."""
from __future__ import annotations

import filecmp
import json
import os
import shutil
from datetime import datetime

import numpy as np

from . import __version__
from .model import Project, Slot

FORMAT = 1
FILE = "flatdeck-project.json"
FILES_DIR = "files"


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


def _pts(a) -> list:
    return [[round(float(x), 6), round(float(y), 6)] for x, y in np.asarray(a, dtype=float).reshape(-1, 2)]


def save_project(project: Project, folder: str, ui: dict | None = None) -> str:
    """Write the project into `folder` (created if needed): the loaded files under files/ and the JSON file. Generated G-code is not
    saved: it is made again from the project. Returns the path of the JSON file. The layers then point at their copies."""
    layers = project.data_layers()
    if not layers:
        raise ProjectError("There is nothing to save: load some files first")
    missing = [f"{l.name} ({l.path or 'no path'})" for l in layers if not l.path or not os.path.isfile(l.path)]
    if missing:
        raise ProjectError("These files are no longer on disk, so they cannot be copied into the project:\n" + "\n".join(missing))
    os.makedirs(os.path.join(folder, FILES_DIR), exist_ok=True)
    used: set = set()
    entries, copies = [], []
    for l in layers:
        dest = _unique(os.path.basename(l.path), used)
        target = os.path.join(folder, FILES_DIR, dest)
        entries.append({
            "file": f"{FILES_DIR}/{dest}", "name": l.name, "kind": l.kind, "side": l.side, "visible": bool(l.visible),
            "hv_points": _pts(l.hv_points) if l.hv_points else [], "ops": [[op, cx, cy] for op, cx, cy in l.ops],
        })
        copies.append((l, l.path, target))
    data = {
        "format": FORMAT, "app_version": __version__, "saved": datetime.now().isoformat(timespec="seconds"),
        "origin_mode": project.origin_mode, "layers": entries,
        "slots": [{"pts": _pts(s.pts), "auto": bool(s.auto)} for s in project.slots],
        "ui": ui or {},
    }
    for l, src, target in copies:                              # copy first: nothing is written if a copy fails
        same = os.path.exists(target) and (os.path.samefile(src, target) or filecmp.cmp(src, target, shallow=False))
        if not same:
            try:
                shutil.copyfile(src, target)
            except OSError as e:
                raise ProjectError(f"Cannot copy {l.name} into the project: {e}") from e
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
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ProjectError(f"The project was saved by a different version of FlatDeck (format {data.get('format') if isinstance(data, dict) else '?'}): "
                           f"this one reads format {FORMAT}")
    project, warnings = Project(), []
    project.origin_mode = data.get("origin_mode", "board") if data.get("origin_mode") in ("board", "file") else "board"
    try:
        for e in data.get("layers", []):
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
    except (KeyError, TypeError, ValueError) as e:
        raise ProjectError(f"The project file is damaged: {e}") from e
    return project, (data.get("ui") if isinstance(data.get("ui"), dict) else {}), warnings
