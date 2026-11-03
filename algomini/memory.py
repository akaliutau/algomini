"""Incremental, section-based research memory with optional local semantic retrieval."""

from collections import defaultdict
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import re
import sqlite3

from .common import digest, sha256, write_json
from . import semantic


FAMILIES = {"csv", "json", "text", "xml", "binary"}
STOP_WORDS = set("a an and are as at be by for from in is it of on or the to with".split())
SCHEMA = 3


def append(root, record, *, run_id="stage3"):
    path = Path(root) / "reports" / run_id / "observations.jsonl"
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
    root = Path(root)
    paths = list((root / "reports").glob("*/observations.jsonl"))
    paths += list((root / "reports/campaigns").glob("*/observations.jsonl"))
    paths += list((root / ".agents/skills").glob("*/SKILL.md"))
    if (root / "laboratory/tools.json").is_file():
        paths.append(root / "laboratory/tools.json")
    return sorted(path for path in paths if path.is_file() and not path.is_symlink())


@contextmanager
def connect(root, cache_dir=None):
    path = (Path(cache_dir) if cache_dir else Path(root) / ".cache/lab") / "index.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
        connection.executescript("DROP TABLE IF EXISTS knowledge; DROP TABLE IF EXISTS metadata; DROP TABLE IF EXISTS sources; DROP TABLE IF EXISTS vectors; DROP TABLE IF EXISTS imports;")
        connection.executescript("""
            CREATE TABLE sources(path TEXT PRIMARY KEY, stamp TEXT, sha256 TEXT);
            CREATE TABLE vectors(model TEXT, hash TEXT, value TEXT, PRIMARY KEY(model,hash));
            CREATE VIRTUAL TABLE knowledge USING fts5(id UNINDEXED,path UNINDEXED,sha256 UNINDEXED,
                kind UNINDEXED,families UNINDEXED,section UNINDEXED,tags,body);
            PRAGMA user_version=3;
        """)
    connection.execute("CREATE TABLE IF NOT EXISTS imports(hash TEXT PRIMARY KEY)")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def documents(path):
    if path.name == "SKILL.md":
        metadata = path.with_name("metadata.json")
        attributes = json.loads(metadata.read_text()) if metadata.exists() else {}
        yield path.parent.name, "skill", attributes.get("families", ["*"]), attributes.get("tags", [path.parent.name]), path.read_text()
    elif path.name == "tools.json":
        for tool in json.loads(path.read_text()):
            yield tool["id"], "tool", tool["families"], [tool["id"]], json.dumps(tool, sort_keys=True)
    else:
        for index, line in enumerate(path.read_text().splitlines()):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("split") == "test":
                continue
            tags = record.get("tags", [])
            families = record.get("families") or sorted(FAMILIES.intersection(tags)) or ["unknown"]
            summary = {key: value for key, value in record.items() if key in (
                "hypothesis", "hypotheses", "outcome", "failure", "mechanism", "interpretation", "scope", "evidence", "probe_ids", "measurements")}
            if not any(key in summary for key in ("hypothesis", "hypotheses", "failure", "mechanism", "interpretation", "probe_ids", "measurements")):
                continue
            if isinstance(summary.get("measurements"), dict):
                summary["measurements"] = {key: value for key, value in summary["measurements"].items() if key != "files"}
            yield str(record.get("id", index)), "observation", families, tags, json.dumps(summary, sort_keys=True)


def sections(body, *, maximum=1400):
    if body.startswith("---\n"):
        body = body.split("\n---", 1)[1].lstrip() if "\n---" in body[4:] else body
    paragraphs = re.split(r"\n\s*\n|(?=^#{1,3} )", body, flags=re.MULTILINE)
    for paragraph in paragraphs:
        for start in range(0, len(paragraph), maximum):
            chunk = paragraph[start:start + maximum].strip()
            if chunk:
                yield chunk


def rebuild(root, *, embedder=None, verify=False, cache_dir=None):
    root = Path(root)
    changed = removed = 0
    with connect(root, cache_dir) as connection:
        if embedder:
            import_vectors(root, connection, embedder)
        previous = dict(connection.execute("SELECT path,stamp FROM sources"))
        present = set()
        for path in sources(root):
            relative = path.relative_to(root).as_posix()
            present.add(relative)
            sidecar = path.with_name("metadata.json") if path.name == "SKILL.md" else None
            stats = [(p.stat().st_mtime_ns, p.stat().st_size) for p in (path, sidecar) if p and p.exists()]
            stamp = json.dumps(stats)
            if previous.get(relative) == stamp and not verify:
                continue
            checksum = sha256(path)
            connection.execute("DELETE FROM knowledge WHERE path=?", (relative,))
            for identifier, kind, families, tags, body in documents(path):
                if not families or any(value not in FAMILIES | {"*", "unknown"} for value in families):
                    raise ValueError("Invalid skill applicability metadata: " + relative)
                connection.executemany("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?)", [
                    (identifier, relative, checksum, kind, "|" + "|".join(families) + "|", index, " ".join(tags), chunk)
                    for index, chunk in enumerate(sections(body))])
            revision = digest((checksum + (sha256(sidecar) if sidecar and sidecar.exists() else "")).encode())
            connection.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?)", (relative, stamp, revision))
            changed += 1
        for relative in set(previous) - present:
            connection.execute("DELETE FROM knowledge WHERE path=?", (relative,))
            connection.execute("DELETE FROM sources WHERE path=?", (relative,))
            removed += 1
        rows = connection.execute("SELECT tags,body FROM knowledge").fetchall()
        if embedder:
            vectors(connection, embedder, [tags + "\n" + body for tags, body in rows])
        snapshot = connection.execute("SELECT path,sha256 FROM sources ORDER BY path").fetchall()
        count = connection.execute("SELECT count(DISTINCT path || ':' || id) FROM knowledge").fetchone()[0]
    return {"path": ".cache/lab/index.sqlite", "documents": count, "sections": len(rows), "updated_sources": changed,
            "removed_sources": removed, "rebuilt": bool(changed or removed), "source_sha256": digest(json.dumps(snapshot).encode()),
            "retrieval": "hybrid" if embedder else "keyword"}


def import_vectors(root, connection, embedder):
    manifest = root / "memory/index-manifest.json"
    if not manifest.is_file():
        return
    identifier = sha256(manifest)
    spec = json.loads(manifest.read_text())
    if spec["embedding_model_id"] != embedder.id or connection.execute("SELECT 1 FROM imports WHERE hash=?", (identifier,)).fetchone():
        return
    path = root / "memory/vectors.jsonl"
    if sha256(path) != spec["files"]["vectors.jsonl"]:
        raise ValueError("Portable semantic index checksum mismatch")
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if not re.fullmatch(r"[0-9a-f]{64}", row["hash"]) or not row["vector"] or any(not math.isfinite(x) for x in row["vector"]):
            raise ValueError("Invalid portable embedding vector")
        connection.execute("INSERT OR IGNORE INTO vectors VALUES(?,?,?)", (embedder.id, row["hash"], json.dumps(row["vector"])))
    connection.execute("INSERT INTO imports VALUES(?)", (identifier,))


def vectors(connection, embedder, texts):
    unique = {digest(text.encode()): text for text in texts}
    cached = dict(connection.execute("SELECT hash,value FROM vectors WHERE model=?", (embedder.id,)))
    missing = [key for key in unique if key not in cached]
    for offset in range(0, len(missing), 64):
        batch = missing[offset:offset + 64]
        for key, vector in zip(batch, embedder.encode([unique[key] for key in batch]), strict=True):
            value = json.dumps(vector, separators=(",", ":"), allow_nan=False)
            connection.execute("INSERT INTO vectors VALUES(?,?,?)", (embedder.id, key, value))
            cached[key] = value
    return [json.loads(cached[digest(text.encode())]) for text in texts]


def search(root, query, *, limit=4, max_chars=6000, family=None, embedder=None, cache_dir=None):
    if not 1 <= limit <= 20 or not 0 < max_chars <= 24000 or family and family not in FAMILIES:
        raise ValueError("Invalid retrieval/context allowance or family")
    terms = [term for term in dict.fromkeys(re.findall(r"[\w-]+", query.lower())) if term not in STOP_WORDS][:16]
    if not terms:
        return []
    embedder = embedder or semantic.load(root)
    rebuild(root, embedder=embedder, cache_dir=cache_dir)
    where, params = ("WHERE families LIKE ? OR families LIKE ?", [f"%|{family}|%", "%|*|%"] ) if family else ("", [])
    with connect(root, cache_dir) as connection:
        rows = connection.execute(f"SELECT rowid,id,path,sha256,kind,section,tags,body FROM knowledge {where}", params).fetchall()
        allowed = {row[0] for row in rows}
        expression = " OR ".join('"' + term + '"' for term in terms)
        lexical = [row[0] for row in connection.execute("SELECT rowid FROM knowledge WHERE knowledge MATCH ? ORDER BY bm25(knowledge),rowid", (expression,)) if row[0] in allowed]
        scores = defaultdict(float)
        for rank, identifier in enumerate(lexical):
            scores[identifier] += 1 / (60 + rank)
        if embedder and rows:
            embedded = vectors(connection, embedder, [row[6] + "\n" + row[7] for row in rows] + [query])
            query_vector = embedded[-1]
            ranked = sorted(zip(rows, embedded[:-1]), key=lambda pair: -sum(a * b for a, b in zip(pair[1], query_vector)))
            for rank, (row, vector) in enumerate(ranked):
                if sum(a * b for a, b in zip(vector, query_vector)) > .15:
                    scores[row[0]] += 1 / (60 + rank)
    results, seen = [], set()
    for row in sorted(rows, key=lambda row: (-scores[row[0]], row[0])):
        identifier, document, path, checksum, kind, section, _, body = row
        if not scores[identifier] or (path, document) in seen:
            continue
        seen.add((path, document))
        results.append({"id": document, "path": path, "source_sha256": checksum, "kind": kind, "section": section,
                        "retrieval": "hybrid" if embedder else "keyword", "embedding_model_id": embedder.id if embedder else None,
                        "excerpt": body[:max_chars // limit]})
        if len(results) == limit:
            break
    return results


def export(root, destination=None, *, embedder=None):
    root = Path(root)
    destination = Path(destination) if destination else root / "memory"
    embedder = embedder or semantic.load(root)
    if (root / "algomini/labtools.py").is_file():
        from .labtools import catalog
        write_json(root / "laboratory/tools.json", catalog())
    state = rebuild(root, embedder=embedder, verify=True)
    destination.mkdir(parents=True, exist_ok=True)
    with connect(root) as connection:
        rows = connection.execute("SELECT id,path,sha256,kind,families,section,tags,body FROM knowledge ORDER BY path,id,section").fetchall()
        columns = ("id", "path", "source_sha256", "kind", "families", "section", "tags", "body")
        (destination / "catalog.jsonl").write_text("".join(json.dumps(dict(zip(columns, row)), sort_keys=True) + "\n" for row in rows))
        if embedder:
            keys = {digest((row[6] + "\n" + row[7]).encode()) for row in rows}
            cached = connection.execute("SELECT hash,value FROM vectors WHERE model=? ORDER BY hash", (embedder.id,)).fetchall()
            (destination / "vectors.jsonl").write_text("".join(json.dumps({"hash": key, "vector": json.loads(value)}) + "\n" for key, value in cached if key in keys))
    files = {name: sha256(destination / name) for name in ("catalog.jsonl", "vectors.jsonl") if (destination / name).is_file()}
    write_json(destination / "index-manifest.json", {"schema_version": 1, "index_schema": SCHEMA, "source_sha256": state["source_sha256"],
               "embedding_model_id": embedder.id if embedder else None, "files": files, "sections": len(rows)})
    return {"ok": True, **state, "export": str(destination)}
