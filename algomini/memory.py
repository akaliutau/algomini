"""Versioned research records and a disposable SQLite FTS5 retrieval index."""

import fcntl
import json
from pathlib import Path
import re
import sqlite3

from .common import digest, sha256


def append(root, record, *, run_id="stage3"):
    path = root / "reports" / run_id / "observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        if any(json.loads(line).get("id") == record["id"] for line in stream if line.strip()):
            return False
        stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
    return True


def sources(root):
    paths = sorted((root / "reports").glob("*/observations.jsonl"))
    paths += sorted((root / ".agents/skills").glob("*/SKILL.md"))
    return [path for path in paths if path.is_file() and not path.is_symlink()]


def rebuild(root):
    directory = root / ".cache/lab"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "index.sqlite"
    paths = sources(root)
    snapshot = {item.relative_to(root).as_posix(): sha256(item) for item in paths}
    signature = digest(json.dumps(snapshot, sort_keys=True).encode())
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5(id UNINDEXED, path UNINDEXED, sha256 UNINDEXED, kind UNINDEXED, tags, body)")
        previous = connection.execute("SELECT value FROM metadata WHERE key='signature'").fetchone()
        changed = previous is None or previous[0] != signature
        if changed:
            connection.execute("DELETE FROM knowledge")
            for item in paths:
                relative = item.relative_to(root).as_posix()
                if item.name == "SKILL.md":
                    records = [(relative, "skill", item.parent.name, item.read_text())]
                else:
                    records = [(str(record.get("id", f"{relative}:{index}")), "observation",
                                " ".join(record.get("tags", [])), json.dumps(record, sort_keys=True))
                               for index, line in enumerate(item.read_text().splitlines()) if line.strip()
                               for record in [json.loads(line)]]
                connection.executemany("INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, ?)",
                                       [(identifier, relative, snapshot[relative], kind, tags, body)
                                        for identifier, kind, tags, body in records])
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('signature', ?)", (signature,))
        count = connection.execute("SELECT count(*) FROM knowledge").fetchone()[0]
    return {"path": path.relative_to(root).as_posix(), "documents": count, "source_sha256": signature, "rebuilt": changed}


def search(root, query, *, limit=4, max_chars=6000):
    if not 1 <= limit <= 20 or not 0 < max_chars <= 24000:
        raise ValueError("Retrieval limits exceed the brief allowance")
    index = rebuild(root)
    terms = list(dict.fromkeys(re.findall(r"[\w-]+", query.lower())))[:16]
    if not terms:
        return []
    expression = " OR ".join('"' + term + '"' for term in terms)
    with sqlite3.connect(root / index["path"]) as connection:
        rows = connection.execute("SELECT id,path,sha256,kind,body FROM knowledge WHERE knowledge MATCH ? ORDER BY bm25(knowledge),id LIMIT ?",
                                  (expression, limit)).fetchall()
    results, remaining = [], max_chars
    for identifier, path, checksum, kind, body in rows:
        if remaining <= 0:
            break
        # Give each finding a share; a large early record cannot hide later skills.
        allowance = min(remaining, max_chars // limit)
        results.append({"id": identifier, "path": path, "source_sha256": checksum, "kind": kind, "excerpt": body[:allowance]})
        remaining -= allowance
    return results
