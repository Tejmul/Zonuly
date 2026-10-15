"""Graph store — nodes, edges and a full-text index in the same SQLite file as everything else.

No graph database. At this scale (a few thousand nodes) a BFS over an indexed edge
table is instant, and keeping it in jobhunter.db means one file to back up and one
place where a job id, a decision id and a session note can point at each other.

Node ids are `kind:slug` — `job:441`, `decision:d4-deterministic-orchestrator`,
`note:2026-08-30-kg-added`. The kind is redundant with the column but makes ids
readable in logs and edges.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jobhunter.db import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_node (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    label      TEXT NOT NULL,
    summary    TEXT,
    props      TEXT NOT NULL DEFAULT '{}',
    layer      TEXT NOT NULL,              -- data | context
    ref_table  TEXT,                       -- data nodes: the row they mirror
    ref_id     INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_kg_node_kind  ON kg_node(kind);
CREATE INDEX IF NOT EXISTS ix_kg_node_layer ON kg_node(layer);
CREATE INDEX IF NOT EXISTS ix_kg_node_ref   ON kg_node(ref_table, ref_id);

CREATE TABLE IF NOT EXISTS kg_edge (
    src        TEXT NOT NULL,
    rel        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    props      TEXT NOT NULL DEFAULT '{}',
    layer      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (src, rel, dst)
);
CREATE INDEX IF NOT EXISTS ix_kg_edge_dst ON kg_edge(dst, rel);
CREATE INDEX IF NOT EXISTS ix_kg_edge_rel ON kg_edge(rel);

CREATE VIRTUAL TABLE IF NOT EXISTS kg_fts USING fts5(
    id UNINDEXED, kind UNINDEXED, label, summary, body,
    tokenize = 'porter unicode61'
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:max_len].rstrip("-") or "x"


def _flatten(value: Any) -> Iterable[str]:
    """Every string leaf of a nested props structure — what the FTS body is made of."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _flatten(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _flatten(v)
    elif value is not None and not isinstance(value, bool):
        yield str(value)


def _row_node(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["props"] = json.loads(d.get("props") or "{}")
    return d


def _row_edge(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["props"] = json.loads(d.get("props") or "{}")
    return d


class Graph:
    """One connection, one transaction per `with` block. Commits on clean exit."""

    def __init__(self, path: Path | str = DB_PATH):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # ADD-COLUMN migration: a fingerprint of the FTS text, so an unchanged node is
        # recognised without touching kg_fts (see upsert_node)
        if "fts_hash" not in {r[1] for r in self.conn.execute("PRAGMA table_info(kg_node)")}:
            self.conn.execute("ALTER TABLE kg_node ADD COLUMN fts_hash TEXT")
            self.conn.commit()

    def __enter__(self) -> "Graph":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # ------------------------------------------------------------------ write

    def upsert_node(
        self,
        id: str,
        kind: str,
        label: str,
        *,
        summary: str | None = None,
        props: dict | None = None,
        layer: str = "context",
        ref_table: str | None = None,
        ref_id: int | None = None,
        body: str | None = None,
    ) -> str:
        props = props or {}
        props_json = json.dumps(props, default=str)
        fts_body = body if body is not None else " ".join(_flatten(props))
        fts_hash = hashlib.sha1(f"{label}\x00{summary or ''}\x00{fts_body}".encode()).hexdigest()
        # Unchanged since the last sync: write nothing. The FTS row is keyed by an
        # UNINDEXED id, so replacing it scans the whole index (~10 ms at 40k nodes);
        # doing that for every node every hour held the write lock for ten minutes.
        cur = self.conn.execute(
            "SELECT kind, label, summary, props, layer, ref_table, ref_id, fts_hash FROM kg_node WHERE id = ?", (id,)
        ).fetchone()
        if cur is not None and tuple(cur) == (kind, label, summary, props_json, layer, ref_table, ref_id, fts_hash):
            return id
        ts = now_iso()
        self.conn.execute(
            """
            INSERT INTO kg_node (id, kind, label, summary, props, layer, ref_table, ref_id, created_at, updated_at,
                                 fts_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind = excluded.kind, label = excluded.label, summary = excluded.summary,
                props = excluded.props, layer = excluded.layer,
                ref_table = excluded.ref_table, ref_id = excluded.ref_id,
                updated_at = excluded.updated_at, fts_hash = excluded.fts_hash
            """,
            (id, kind, label, summary, props_json, layer, ref_table, ref_id, ts, ts, fts_hash),
        )
        self.conn.execute("DELETE FROM kg_fts WHERE id = ?", (id,))
        self.conn.execute(
            "INSERT INTO kg_fts (id, kind, label, summary, body) VALUES (?, ?, ?, ?, ?)",
            (id, kind, label, summary or "", fts_body),
        )
        return id

    def upsert_edge(self, src: str, rel: str, dst: str, *, props: dict | None = None, layer: str = "context") -> None:
        self.conn.execute(
            """
            INSERT INTO kg_edge (src, rel, dst, props, layer, created_at) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(src, rel, dst) DO UPDATE SET props = excluded.props, layer = excluded.layer
            """,
            (src, rel, dst, json.dumps(props or {}, default=str), layer, now_iso()),
        )

    def delete_node(self, id: str) -> None:
        self.conn.execute("DELETE FROM kg_edge WHERE src = ? OR dst = ?", (id, id))
        self.conn.execute("DELETE FROM kg_fts WHERE id = ?", (id,))
        self.conn.execute("DELETE FROM kg_node WHERE id = ?", (id,))

    def prune(self, kind: str, keep_ids: set[str]) -> int:
        """Drop nodes of a kind that are no longer in `keep_ids` — rows deleted upstream."""
        stale = [
            r["id"] for r in self.conn.execute("SELECT id FROM kg_node WHERE kind = ?", (kind,))
            if r["id"] not in keep_ids
        ]
        for nid in stale:
            self.delete_node(nid)
        return len(stale)

    def clear_layer(self, layer: str) -> None:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM kg_node WHERE layer = ?", (layer,))]
        for nid in ids:
            self.delete_node(nid)
        self.conn.execute("DELETE FROM kg_edge WHERE layer = ?", (layer,))

    def remember(self, text: str, *, about: Iterable[str] = (), tags: Iterable[str] = (), title: str | None = None) -> dict:
        """Add a dated session note. This is how context accumulates between sessions."""
        ts = now_iso()
        date = ts[:10]
        title = (title or text).strip().splitlines()[0][:80]
        nid = f"note:{date}-{slugify(title, 50)}"
        # a second note with the same title on the same day gets a suffix, not an overwrite
        base, n = nid, 2
        while self.get(nid) is not None:
            nid = f"{base}-{n}"
            n += 1
        self.upsert_node(
            nid, "note", title,
            summary=text.strip(),
            props={"date": date, "tags": sorted(set(tags)), "about": sorted(set(about))},
            body=text,
        )
        missing = []
        for target in about:
            if self.get(target) is None:
                missing.append(target)
                continue
            self.upsert_edge(nid, "ABOUT", target)
        return {"id": nid, "date": date, "missing_targets": missing}

    # ------------------------------------------------------------------- read

    def get(self, id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM kg_node WHERE id = ?", (id,)).fetchone()
        return _row_node(row) if row else None

    def nodes(self, *, kind: str | None = None, layer: str | None = None, limit: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM kg_node", []
        conds = []
        if kind:
            conds.append("kind = ?")
            args.append(kind)
        if layer:
            conds.append("layer = ?")
            args.append(layer)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY kind, id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_row_node(r) for r in self.conn.execute(sql, args)]

    def edges(self, id: str, *, direction: str = "both", rel: str | None = None) -> list[dict]:
        out: list[dict] = []
        if direction in ("out", "both"):
            sql, args = "SELECT * FROM kg_edge WHERE src = ?", [id]
            if rel:
                sql += " AND rel = ?"
                args.append(rel)
            out += [_row_edge(r) for r in self.conn.execute(sql, args)]
        if direction in ("in", "both"):
            sql, args = "SELECT * FROM kg_edge WHERE dst = ?", [id]
            if rel:
                sql += " AND rel = ?"
                args.append(rel)
            out += [_row_edge(r) for r in self.conn.execute(sql, args)]
        return out

    def all_edges(self, *, layer: str | None = None, rel: str | None = None) -> list[dict]:
        sql, args, conds = "SELECT * FROM kg_edge", [], []
        if layer:
            conds.append("layer = ?")
            args.append(layer)
        if rel:
            conds.append("rel = ?")
            args.append(rel)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return [_row_edge(r) for r in self.conn.execute(sql, args)]

    def neighbors(
        self,
        id: str,
        *,
        depth: int = 1,
        rel: str | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 300,
    ) -> dict:
        """BFS out to `depth` hops in both directions. Returns {nodes, edges} with the root first."""
        root = self.get(id)
        if root is None:
            return {"nodes": [], "edges": []}
        kinds = set(kinds) if kinds else None
        seen: dict[str, dict] = {id: root}
        edges: dict[tuple, dict] = {}
        frontier = [id]
        for _ in range(depth):
            nxt: list[str] = []
            for nid in frontier:
                for e in self.edges(nid, rel=rel):
                    other = e["dst"] if e["src"] == nid else e["src"]
                    node = seen.get(other) or self.get(other)
                    if node is None or (kinds and node["kind"] not in kinds and other != id):
                        continue
                    edges[(e["src"], e["rel"], e["dst"])] = e
                    if other not in seen:
                        seen[other] = node
                        nxt.append(other)
                    if len(seen) >= limit:
                        break
                if len(seen) >= limit:
                    break
            frontier = nxt
            if not frontier or len(seen) >= limit:
                break
        return {"nodes": list(seen.values()), "edges": list(edges.values())}

    def path(self, a: str, b: str, *, max_depth: int = 6) -> list[dict]:
        """Shortest undirected path as alternating node/edge dicts. Empty if none within max_depth."""
        if self.get(a) is None or self.get(b) is None:
            return []
        prev: dict[str, tuple[str, dict] | None] = {a: None}
        q = deque([(a, 0)])
        while q:
            nid, d = q.popleft()
            if nid == b:
                break
            if d >= max_depth:
                continue
            for e in self.edges(nid):
                other = e["dst"] if e["src"] == nid else e["src"]
                if other not in prev:
                    prev[other] = (nid, e)
                    q.append((other, d + 1))
        if b not in prev:
            return []
        steps: list[dict] = []
        cur = b
        while cur != a:
            parent, edge = prev[cur]  # type: ignore[misc]
            steps.append(self.get(cur))  # type: ignore[arg-type]
            steps.append({"edge": edge})
            cur = parent
        steps.append(self.get(a))  # type: ignore[arg-type]
        steps.reverse()
        return steps

    def search(self, q: str, *, kinds: Iterable[str] | None = None, limit: int = 20, mode: str = "and") -> list[dict]:
        """Full-text search. `mode='or'` ranks by overlap; `'and'` requires every term."""
        query = fts_query(q, mode)
        if not query:
            return []
        sql = "SELECT id, bm25(kg_fts) AS rank FROM kg_fts WHERE kg_fts MATCH ?"
        args: list[Any] = [query]
        kinds = list(kinds) if kinds else []
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += kinds
        sql += " ORDER BY rank LIMIT ?"
        args.append(int(limit))
        try:
            rows = self.conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            node = self.get(r["id"])
            if node:
                node["rank"] = -float(r["rank"])  # bm25 is negative-better; flip so bigger is better
                out.append(node)
        return out

    def subgraph(self, *, layer: str | None = None, kinds: Iterable[str] | None = None) -> dict:
        nodes = self.nodes(layer=layer)
        if kinds:
            ks = set(kinds)
            nodes = [n for n in nodes if n["kind"] in ks]
        ids = {n["id"] for n in nodes}
        edges = [e for e in self.all_edges() if e["src"] in ids and e["dst"] in ids]
        return {"nodes": nodes, "edges": edges}

    def stats(self) -> dict:
        kinds = {r["kind"]: r["n"] for r in self.conn.execute("SELECT kind, COUNT(*) n FROM kg_node GROUP BY kind ORDER BY n DESC")}
        rels = {r["rel"]: r["n"] for r in self.conn.execute("SELECT rel, COUNT(*) n FROM kg_edge GROUP BY rel ORDER BY n DESC")}
        layers = {r["layer"]: r["n"] for r in self.conn.execute("SELECT layer, COUNT(*) n FROM kg_node GROUP BY layer")}
        return {
            "nodes": sum(kinds.values()),
            "edges": sum(rels.values()),
            "layers": layers,
            "kinds": kinds,
            "relations": rels,
        }


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-\.]*")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "be", "it", "this",
    "that", "with", "as", "at", "by", "we", "you", "i", "not", "but", "from", "our", "your", "can",
    "do", "does", "so", "if", "than", "then", "into", "one", "all", "any", "no", "yes",
}


def tokens(text: str) -> list[str]:
    return [t.lower().strip(".-_") for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOP and len(t) > 1]


def fts_query(q: str, mode: str = "and") -> str:
    """Quote every token so user text can never be an FTS5 syntax error."""
    toks = tokens(q)
    if not toks:
        return ""
    quoted = ['"%s"' % t.replace('"', "") for t in toks]
    return (" OR " if mode == "or" else " ").join(quoted)
