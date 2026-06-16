"""ROM directory scanner — walks /mnt/seagate/ROMs/{sys}/ and populates SQLite."""
from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path
from typing import Dict, List

import yaml


def load_cores(cores_path: Path) -> Dict[str, dict]:
    with cores_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def crc32_of(path: Path) -> str:
    h = 0
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h = zlib.crc32(chunk, h)
    return f"{h & 0xFFFFFFFF:08x}"


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS roms (
          id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          system TEXT NOT NULL,
          filename TEXT NOT NULL,
          file_ext TEXT NOT NULL,
          file_size INTEGER NOT NULL,
          crc32 TEXT,
          boxart_status TEXT DEFAULT 'unknown',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(system, filename)
        );
        CREATE INDEX IF NOT EXISTS idx_roms_system ON roms(system);
        CREATE INDEX IF NOT EXISTS idx_roms_title ON roms(title);

        CREATE TABLE IF NOT EXISTS saves (
          device_id TEXT NOT NULL,
          rom_id INTEGER NOT NULL,
          state BLOB,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(device_id, rom_id),
          FOREIGN KEY (rom_id) REFERENCES roms(id)
        );
        """
    )
    conn.commit()
    return conn


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    # strip common tags like (USA), [!], (Rev A)
    for sep in ("(", "["):
        if sep in stem:
            stem = stem.split(sep, 1)[0]
    return stem.replace("_", " ").replace("  ", " ").strip()


def scan(roms_root: Path, db_path: Path, cores_path: Path) -> dict:
    cores = load_cores(cores_path)
    conn = init_db(db_path)
    roms_root.mkdir(parents=True, exist_ok=True)

    inserted = 0
    updated = 0
    seen: set[tuple[str, str]] = set()

    for system, cfg in cores.items():
        exts = {e.lower() for e in cfg.get("exts", [])}
        sys_dir = roms_root / system
        if not sys_dir.exists():
            sys_dir.mkdir(parents=True, exist_ok=True)
            continue
        for path in sorted(sys_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in exts:
                continue
            key = (system, path.name)
            seen.add(key)

            title = title_from_filename(path.name)
            size = path.stat().st_size
            try:
                crc = crc32_of(path)
            except OSError:
                crc = None

            cur = conn.execute(
                "SELECT id FROM roms WHERE system=? AND filename=?",
                (system, path.name),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    "UPDATE roms SET title=?, file_ext=?, file_size=?, crc32=? WHERE id=?",
                    (title, path.suffix.lower().lstrip("."), size, crc, row[0]),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO roms(title, system, filename, file_ext, file_size, crc32, boxart_status)
                       VALUES (?, ?, ?, ?, ?, ?, 'unknown')""",
                    (title, system, path.name, path.suffix.lower().lstrip("."), size, crc),
                )
                inserted += 1

    # prune missing files
    cur = conn.execute("SELECT id, system, filename FROM roms")
    for rom_id, system, filename in cur.fetchall():
        if (system, filename) not in seen:
            conn.execute("DELETE FROM roms WHERE id=?", (rom_id,))

    conn.commit()
    conn.close()
    return {"inserted": inserted, "updated": updated, "total": inserted + updated}
