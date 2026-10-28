#!/usr/bin/env python3
"""Offline compatibility ledger from the EditRoast v2 integration preview.

This small SQLite ledger preserves the preview's revision, finding, and
translation-decision CLI for package compatibility.  It is not the final
ER-010 project schema and it makes no network requests.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DECISION_STATUSES = ("approved", "rejected")
FINDING_STATUSES = ("accepted", "rejected", "applied", "verified", "obsolete")


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_root(path: str | Path) -> Path:
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def database(root: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(root / "editroast.sqlite")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS revisions(
            id INTEGER PRIMARY KEY,
            parent INTEGER REFERENCES revisions(id),
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            created TEXT NOT NULL,
            note TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions(
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            context TEXT NOT NULL,
            alternatives TEXT NOT NULL,
            selected TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
            created TEXT NOT NULL,
            updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS findings(
            id TEXT PRIMARY KEY,
            revision_id INTEGER NOT NULL REFERENCES revisions(id),
            anchor TEXT NOT NULL,
            quote TEXT NOT NULL,
            recommendation TEXT NOT NULL,
            status TEXT NOT NULL CHECK(
                status IN ('proposed','accepted','rejected','applied','verified','obsolete')
            ),
            created TEXT NOT NULL
        );
        """
    )
    return connection


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    actions = parser.add_subparsers(dest="action", required=True)

    command = actions.add_parser("import")
    command.add_argument("file")
    command.add_argument("--note", default="Author source")

    command = actions.add_parser("decision")
    command.add_argument("--id", required=True)
    command.add_argument("--kind", required=True)
    command.add_argument("--source", required=True)
    command.add_argument("--context", default="")
    command.add_argument("--alternatives", required=True, help="JSON array")

    command = actions.add_parser("decide")
    command.add_argument("--id", required=True)
    command.add_argument("--status", required=True, choices=DECISION_STATUSES)
    command.add_argument("--selected", default="")

    command = actions.add_parser("finding")
    command.add_argument("--id", required=True)
    command.add_argument("--revision", required=True, type=int)
    command.add_argument("--anchor", required=True)
    command.add_argument("--quote", required=True)
    command.add_argument("--recommendation", required=True)

    command = actions.add_parser("finding-status")
    command.add_argument("--id", required=True)
    command.add_argument("--status", required=True, choices=FINDING_STATUSES)

    actions.add_parser("status")
    actions.add_parser("translation-gate")
    actions.add_parser("export-decisions")
    return parser


def import_revision(connection: sqlite3.Connection, root: Path, args: argparse.Namespace) -> None:
    source = Path(args.file).resolve()
    require(source.is_file(), "Source file missing")
    latest = connection.execute("SELECT id FROM revisions ORDER BY id DESC LIMIT 1").fetchone()
    parent = latest[0] if latest else None
    snapshots = root / "revisions"
    snapshots.mkdir(exist_ok=True)
    sha256 = digest(source)
    destination = snapshots / f"{sha256}{source.suffix.lower()}"
    if not destination.exists():
        shutil.copy2(source, destination)
    cursor = connection.execute(
        "INSERT INTO revisions(parent,filename,sha256,created,note) VALUES(?,?,?,?,?)",
        (parent, destination.relative_to(root).as_posix(), sha256, stamp(), args.note),
    )
    print(
        json.dumps(
            {
                "revision": cursor.lastrowid,
                "parent": parent,
                "sha256": sha256,
                "snapshot": str(destination),
            }
        )
    )


def record_decision(connection: sqlite3.Connection, args: argparse.Namespace) -> None:
    alternatives = json.loads(args.alternatives)
    require(isinstance(alternatives, list) and alternatives, "Nonempty JSON array required")
    now = stamp()
    connection.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
        (
            args.id,
            args.kind,
            args.source,
            args.context,
            json.dumps(alternatives, ensure_ascii=False),
            None,
            "pending",
            now,
            now,
        ),
    )
    print("Decision recorded as pending:", args.id)


def decide(connection: sqlite3.Connection, args: argparse.Namespace) -> None:
    row = connection.execute(
        "SELECT alternatives FROM decisions WHERE id=?", (args.id,)
    ).fetchone()
    require(row, "Unknown decision ID")
    alternatives = json.loads(row[0])
    require(
        args.status != "approved" or args.selected in alternatives,
        "Approved choice must match a listed alternative",
    )
    connection.execute(
        "UPDATE decisions SET status=?,selected=?,updated=? WHERE id=?",
        (args.status, args.selected or None, stamp(), args.id),
    )
    print("Updated:", args.id, args.status)


def record_finding(connection: sqlite3.Connection, args: argparse.Namespace) -> None:
    revision = connection.execute(
        "SELECT 1 FROM revisions WHERE id=?", (args.revision,)
    ).fetchone()
    require(revision, "Revision missing")
    connection.execute(
        "INSERT INTO findings VALUES(?,?,?,?,?,?,?)",
        (
            args.id,
            args.revision,
            args.anchor,
            args.quote,
            args.recommendation,
            "proposed",
            stamp(),
        ),
    )
    print("Finding created:", args.id)


def update_finding(connection: sqlite3.Connection, args: argparse.Namespace) -> None:
    cursor = connection.execute(
        "UPDATE findings SET status=? WHERE id=?", (args.status, args.id)
    )
    require(cursor.rowcount, "Unknown finding ID")
    print("Finding status:", args.id, args.status)


def translation_gate(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        "SELECT id FROM decisions WHERE status='pending' ORDER BY id"
    ).fetchall()
    print(json.dumps({"ready": not bool(rows), "pending": [row[0] for row in rows]}))
    return 2 if rows else 0


def export_decisions(connection: sqlite3.Connection, root: Path) -> None:
    path = root / "translation_decisions.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["id", "kind", "source", "context", "alternatives", "selected", "status"]
        )
        writer.writerows(
            connection.execute(
                "SELECT id,kind,source,context,alternatives,selected,status "
                "FROM decisions ORDER BY id"
            )
        )
    print(path)


def show_status(connection: sqlite3.Connection) -> None:
    payload = {
        "revisions": connection.execute(
            "SELECT id,parent,sha256,note FROM revisions"
        ).fetchall(),
        "decisions": connection.execute(
            "SELECT id,status,selected FROM decisions"
        ).fetchall(),
        "findings": connection.execute(
            "SELECT id,revision_id,status FROM findings"
        ).fetchall(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run(args: argparse.Namespace) -> int:
    root = project_root(args.project)
    with database(root) as connection:
        if args.action == "import":
            import_revision(connection, root, args)
        elif args.action == "decision":
            record_decision(connection, args)
        elif args.action == "decide":
            decide(connection, args)
        elif args.action == "finding":
            record_finding(connection, args)
        elif args.action == "finding-status":
            update_finding(connection, args)
        elif args.action == "translation-gate":
            return translation_gate(connection)
        elif args.action == "export-decisions":
            export_decisions(connection, root)
        else:
            show_status(connection)
    return 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except (ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
