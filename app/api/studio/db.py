"""SQLite connection, versioned migrations and small helpers."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE sources (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,              -- video|audio|image|document|note|url|page|account|archive|other
      title TEXT,
      original_name TEXT,
      author TEXT DEFAULT 'неизвестно',
      platform TEXT DEFAULT 'неизвестно',
      url TEXT,                        -- canonical URL if the source *is* a link
      rel_path TEXT,                   -- original file, relative to DATA_DIR
      size INTEGER,
      mime TEXT,
      sha256 TEXT,
      imported_at TEXT NOT NULL,
      published_at TEXT DEFAULT 'неизвестно',
      language TEXT DEFAULT 'неизвестно',
      duration REAL,
      purpose TEXT DEFAULT 'обучение',  -- обучение|референс|ассет|мой материал|сочетание
      usage_status TEXT DEFAULT 'неизвестно', -- мой материал|разрешено|только референс|неизвестно
      provenance TEXT,
      brand_id TEXT,
      parent_id TEXT,                  -- previous version / containing archive / account
      version INTEGER DEFAULT 1,
      is_test INTEGER DEFAULT 0,
      st_acquire TEXT DEFAULT 'pending',
      st_speech TEXT DEFAULT 'n/a',
      st_visual TEXT DEFAULT 'n/a',
      st_audio TEXT DEFAULT 'n/a',
      st_analysis TEXT DEFAULT 'n/a',
      st_knowledge TEXT DEFAULT 'n/a',
      blocked_reason TEXT,
      next_action TEXT,
      warnings TEXT DEFAULT '[]',
      tags TEXT DEFAULT '[]',
      notes TEXT DEFAULT '',
      stale INTEGER DEFAULT 0
    );
    CREATE INDEX ix_sources_sha ON sources(sha256);
    CREATE UNIQUE INDEX ux_sources_url ON sources(url) WHERE url IS NOT NULL AND rel_path IS NULL;

    CREATE TABLE source_links (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id TEXT NOT NULL REFERENCES sources(id),
      url TEXT,
      via TEXT,                        -- how it reached the studio (upload, telegram, collection:X, user-indicated)
      note TEXT,
      added_at TEXT NOT NULL
    );
    CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at TEXT);
    CREATE TABLE source_collections (source_id TEXT, collection_id TEXT, PRIMARY KEY(source_id, collection_id));
    CREATE TABLE source_relations (a TEXT, b TEXT, rel TEXT, note TEXT, PRIMARY KEY(a,b,rel));

    CREATE TABLE accounts (
      id TEXT PRIMARY KEY, platform TEXT, handle TEXT, url TEXT UNIQUE,
      snapshot_at TEXT, period TEXT, sample_note TEXT, status TEXT, notes TEXT DEFAULT ''
    );
    CREATE TABLE account_posts (account_id TEXT, source_id TEXT, studied INTEGER DEFAULT 0, PRIMARY KEY(account_id, source_id));

    CREATE TABLE jobs (
      id TEXT PRIMARY KEY,
      key TEXT UNIQUE NOT NULL,        -- sha256-of-source + stage + stage version
      source_id TEXT NOT NULL,
      stage TEXT NOT NULL,
      depends_on TEXT DEFAULT '[]',
      state TEXT NOT NULL DEFAULT 'queued', -- queued|running|completed|partial|blocked|failed|cancelled
      attempts INTEGER DEFAULT 0,
      max_attempts INTEGER DEFAULT 3,
      run_id TEXT,
      created_at TEXT, started_at TEXT, finished_at TEXT, heartbeat REAL,
      note TEXT, error TEXT, paid INTEGER DEFAULT 0
    );
    CREATE INDEX ix_jobs_state ON jobs(state);

    CREATE TABLE artifacts (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL, run_id TEXT, stage TEXT, kind TEXT,
      rel_path TEXT, t REAL, meta TEXT DEFAULT '{}', created_at TEXT
    );
    CREATE INDEX ix_art_source ON artifacts(source_id, kind);

    CREATE TABLE analyses (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version INTEGER NOT NULL,
      origin TEXT NOT NULL,            -- claude-manual | provider:<name> | user
      provider TEXT, model TEXT, prompt_version TEXT,
      created_at TEXT, summary TEXT, data TEXT NOT NULL, validation TEXT, rel_dir TEXT,
      UNIQUE(source_id, version)
    );

    CREATE TABLE knowledge (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, category TEXT NOT NULL,
      statement TEXT, task TEXT, when_use TEXT, when_not TEXT, conditions TEXT,
      steps TEXT DEFAULT '[]', example TEXT, mistake TEXT,
      evidence TEXT NOT NULL,          -- author_explained|observed|hypothesis|user_preference|project_confirmed
      limitations TEXT, brand_scope TEXT DEFAULT 'all', format_scope TEXT, language TEXT,
      tags TEXT DEFAULT '[]', origin TEXT, status TEXT DEFAULT 'active',
      version INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, is_test INTEGER DEFAULT 0
    );
    CREATE TABLE knowledge_refs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_id TEXT, source_id TEXT,
      t_start REAL, t_end REAL, page TEXT, artifact_id TEXT, quote TEXT, note TEXT
    );
    CREATE TABLE knowledge_versions (
      knowledge_id TEXT, version INTEGER, data TEXT, changed_at TEXT, note TEXT, author TEXT,
      PRIMARY KEY(knowledge_id, version)
    );

    CREATE TABLE brands (id TEXT PRIMARY KEY, name TEXT UNIQUE, data TEXT, version INTEGER DEFAULT 1, updated_at TEXT);
    CREATE TABLE preferences (
      id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_id TEXT, text TEXT NOT NULL,
      active INTEGER DEFAULT 1, replaced_by TEXT, created_at TEXT, origin TEXT DEFAULT 'user'
    );

    CREATE TABLE projects (
      id TEXT PRIMARY KEY, title TEXT, brand_id TEXT, mode TEXT, status TEXT DEFAULT 'brief',
      brief TEXT, created_at TEXT, updated_at TEXT, is_test INTEGER DEFAULT 0
    );
    CREATE TABLE project_docs (
      project_id TEXT, doc TEXT, version INTEGER, content TEXT, origin TEXT,
      knowledge_ids TEXT DEFAULT '[]', source_ids TEXT DEFAULT '[]', created_at TEXT, note TEXT,
      PRIMARY KEY(project_id, doc, version)
    );

    CREATE TABLE imports (
      id TEXT PRIMARY KEY, kind TEXT, target TEXT, status TEXT, errors TEXT, payload_rel TEXT, at TEXT
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);

    CREATE VIRTUAL TABLE search USING fts5(
      etype UNINDEXED, eid UNINDEXED, sid UNINDEXED, t UNINDEXED, title, body,
      tokenize = 'unicode61 remove_diacritics 2'
    );
    """,
    # 2 — review board: generated assets (by path, read-only) + owner decisions
    """
    CREATE TABLE review_items (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,              -- sticker_xerox|sticker_pixel|video|sound|doc
      grp TEXT NOT NULL,
      title TEXT,
      root TEXT NOT NULL,              -- key from config/review_sets.json
      rel_path TEXT NOT NULL,          -- relative to that root
      ext TEXT, size INTEGER, mtime REAL,
      status TEXT NOT NULL DEFAULT 'new', -- new|selected|rejected|needs_changes
      comment TEXT DEFAULT '',
      tags TEXT DEFAULT '[]',
      forward INTEGER DEFAULT 0,       -- 1 = owner sent it to Claude (pending request)
      forward_at TEXT,
      handled_at TEXT, handled_note TEXT,
      decided_at TEXT,                 -- last owner change (status/comment/tags/forward)
      first_seen TEXT, last_seen TEXT, file_changed_at TEXT,
      missing INTEGER DEFAULT 0, trashed_at TEXT,
      UNIQUE(root, rel_path)
    );
    CREATE INDEX ix_review_kind ON review_items(kind, grp);
    CREATE TABLE review_groups (
      id TEXT PRIMARY KEY,             -- rg:<kind>:<grp>
      kind TEXT NOT NULL, grp TEXT NOT NULL,
      comment TEXT DEFAULT '', forward INTEGER DEFAULT 0, forward_at TEXT,
      handled_at TEXT, handled_note TEXT, decided_at TEXT
    );
    CREATE TABLE review_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, action TEXT NOT NULL,
      data TEXT DEFAULT '{}', actor TEXT DEFAULT 'owner', at TEXT NOT NULL
    );
    CREATE INDEX ix_review_events ON review_events(item_id);
    """,
    # 3 — organic clipping workspace (USD cents)
    """
    CREATE TABLE clip_campaigns (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, platform TEXT NOT NULL,
      url TEXT NOT NULL, rules TEXT NOT NULL, rights TEXT NOT NULL,
      confirmed INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'draft',
      deadline TEXT NOT NULL DEFAULT '', cpm_cents INTEGER NOT NULL,
      min_views INTEGER NOT NULL, cap_cents INTEGER NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE clipping_items (
      id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES clip_campaigns(id),
      source_id TEXT NOT NULL REFERENCES sources(id), t0 REAL NOT NULL, t1 REAL NOT NULL,
      hook TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned',
      output_rel TEXT, captions_rel TEXT, error TEXT, post_url TEXT,
      eligible_views INTEGER NOT NULL DEFAULT 0, cost_cents INTEGER NOT NULL DEFAULT 0,
      paid_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(campaign_id,source_id,t0,t1)
    );
    CREATE UNIQUE INDEX ux_clipping_post ON clipping_items(post_url) WHERE post_url IS NOT NULL;
    """,
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%y%m%d')}_{secrets.token_hex(3)}"


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def migrate(con: sqlite3.Connection) -> int:
    cur = con.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[cur:], start=cur + 1):
        # executescript runs the whole migration atomically together with the version bump
        con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={i};\nCOMMIT;")
    return len(MIGRATIONS)


@contextmanager
def tx(con: sqlite3.Connection):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def open_db() -> sqlite3.Connection:
    config.ensure_dirs()
    con = connect()
    migrate(con)
    return con


def row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("warnings", "tags", "steps", "depends_on", "meta", "data", "validation", "brief",
              "knowledge_ids", "source_ids", "errors"):
        if k in d and isinstance(d[k], str) and d[k][:1] in "[{":
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def rows(rs) -> list[dict]:
    return [row(r) for r in rs]


def j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def index(con, etype: str, eid: str, title: str, body: str, sid: str | None = None, t: float | None = None):
    """Replace search entries for an entity (called inside caller's transaction)."""
    con.execute("DELETE FROM search WHERE etype=? AND eid=?", (etype, eid))
    con.execute("INSERT INTO search(etype,eid,sid,t,title,body) VALUES (?,?,?,?,?,?)",
                (etype, eid, sid, t, title or "", body or ""))
