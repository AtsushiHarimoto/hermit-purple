"""
Hermit Purple Infrastructure: Storage Layer (Knowledge Base)
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config import get_config

class KnowledgeBase:
    """
    SQLite-based persistent storage with FTS5 support.
    Stores raw resources and AI analysis results.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            config = get_config()
            # Default to data/vibedev.db relative to tool root
            # tool root is 2 levels up from src/infra
            root = Path(__file__).parents[2]
            db_path = str(root / "data" / "hermit.db")
            
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        
    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
        
    def _init_db(self):
        """Initialize schema with FTS support"""
        with self._get_conn() as conn:
            # 1. Resources Table (Raw Data)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,       -- URL hash or External ID
                url TEXT NOT NULL,
                title TEXT,
                description TEXT,
                author TEXT,
                platform TEXT,             -- github, reddit, etc.
                raw_content TEXT,          -- Full scraped text
                created_at DATETIME,
                scraped_at DATETIME
            )
            """)
            
            # 2. Analysis Table (AI Insights - Decision Support)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis (
                resource_id TEXT PRIMARY KEY,
                verdict TEXT,              -- Adopt, Trial, Assess, Ignore
                score INTEGER,             -- 0-100 Relevance Score
                summary TEXT,              -- Human-readable summary
                tags TEXT,                 -- JSON list
                risks TEXT,                -- JSON list (SWOT: Weaknesses/Threats)
                value_prop TEXT,           -- JSON list (SWOT: Strengths/Opportunities)
                analyzed_at DATETIME,
                FOREIGN KEY(resource_id) REFERENCES resources(id)
            )
            """)

            # 4. Social Comments Table (For deep sentiment analysis)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS social_comments (
                id TEXT PRIMARY KEY,
                resource_id TEXT,
                author TEXT,
                content TEXT NOT NULL,
                likes INTEGER DEFAULT 0,
                sentiment TEXT,            -- Positive, Negative, Neutral (Calculated)
                sentiment_score REAL,      -- -1.0 to 1.0 (Calculated)
                created_at DATETIME,
                FOREIGN KEY(resource_id) REFERENCES resources(id)
            )
            """)

            # 5. Market Sentiment Summary (Aggregated)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS market_sentiment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                overall_score REAL,
                willingness_to_pay REAL,   -- Estimated 0.0-1.0
                demand_signals TEXT,       -- JSON list
                pain_points TEXT,          -- JSON list
                updated_at DATETIME
            )
            """)
            
            self._ensure_fts(conn)

    def _ensure_fts(self, conn: sqlite3.Connection):
        expected_cols = ["resource_id", "title", "description", "summary"]
        try:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(resources_fts)")]
            if cols == expected_cols:
                return
        except sqlite3.OperationalError:
            pass

        conn.execute("DROP TABLE IF EXISTS resources_fts")
        conn.execute("""
        CREATE VIRTUAL TABLE resources_fts USING fts5(
            resource_id UNINDEXED,
            title,
            description,
            summary,
            tokenize='porter'
        )
        """)
        self._rebuild_fts(conn)

    def _rebuild_fts(self, conn: sqlite3.Connection):
        rows = conn.execute("""
            SELECT r.id AS resource_id, r.title, r.description, a.summary
            FROM resources r
            LEFT JOIN analysis a ON r.id = a.resource_id
        """).fetchall()
        for row in rows:
            conn.execute("""
                INSERT INTO resources_fts(resource_id, title, description, summary)
                VALUES (?, ?, ?, ?)
            """, (row["resource_id"], row["title"], row["description"], row["summary"]))

    def _upsert_fts(
        self,
        conn: sqlite3.Connection,
        resource_id: str,
        title: Optional[str],
        description: Optional[str],
        summary: Optional[str]
    ):
        conn.execute("DELETE FROM resources_fts WHERE resource_id = ?", (resource_id,))
        conn.execute("""
            INSERT INTO resources_fts(resource_id, title, description, summary)
            VALUES (?, ?, ?, ?)
        """, (resource_id, title, description, summary))
            
    def upsert_resource(self, data: Dict[str, Any]) -> str:
        """Insert or Update a resource record"""
        now = datetime.now(timezone.utc).isoformat()
        
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO resources 
            (id, url, title, description, author, platform, raw_content, created_at, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["id"],
                data.get("url"),
                data.get("title"),
                data.get("description"),
                data.get("author"),
                data.get("platform"),
                data.get("raw_content"),
                data.get("created_at", now),
                now
            ))
            self._upsert_fts(
                conn,
                data["id"],
                data.get("title"),
                data.get("description"),
                None
            )
        return data["id"]
        
    def add_analysis(self, resource_id: str, analysis: Dict[str, Any]):
        """Save AI analysis results"""
        now = datetime.now(timezone.utc).isoformat()
        
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO analysis
            (resource_id, verdict, score, summary, tags, risks, value_prop, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                resource_id,
                analysis.get("verdict"),
                analysis.get("score", 0),
                analysis.get("summary"),
                json.dumps(analysis.get("tags", [])),
                json.dumps(analysis.get("risks", [])),
                json.dumps(analysis.get("value_prop", [])),
                now
            ))
            row = conn.execute(
                "SELECT title, description FROM resources WHERE id = ?",
                (resource_id,)
            ).fetchone()
            if row:
                self._upsert_fts(
                    conn,
                    resource_id,
                    row["title"],
                    row["description"],
                    analysis.get("summary")
                )

    def search_similar(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Find similar resources using FTS (Keyword based for now)"""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT r.title, r.url, a.summary, a.verdict 
                FROM resources r
                LEFT JOIN analysis a ON r.id = a.resource_id
                WHERE r.id IN (
                    SELECT resource_id
                    FROM resources_fts
                    WHERE resources_fts MATCH ?
                    ORDER BY bm25(resources_fts)
                    LIMIT ?
                )
                LIMIT ?
            """, (query, limit, limit))
            return [dict(row) for row in cursor.fetchall()]

    def add_social_comment(self, resource_id: str, comment_data: Dict[str, Any]):
        """Save a social media comment"""
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO social_comments
            (id, resource_id, author, content, likes, sentiment, sentiment_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                comment_data.get("id") or f"{resource_id}_{datetime.now().timestamp()}",
                resource_id,
                comment_data.get("author"),
                comment_data["content"],
                comment_data.get("likes", 0),
                comment_data.get("sentiment"),
                comment_data.get("sentiment_score"),
                comment_data.get("created_at")
            ))

    def update_market_sentiment(self, topic: str, sentiment_data: Dict[str, Any]):
        """Update aggregated market sentiment for a topic"""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
            INSERT INTO market_sentiment
            (topic, overall_score, willingness_to_pay, demand_signals, pain_points, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                topic,
                sentiment_data.get("overall_score", 0.0),
                sentiment_data.get("willingness_to_pay", 0.0),
                json.dumps(sentiment_data.get("demand_signals", [])),
                json.dumps(sentiment_data.get("pain_points", [])),
                now
            ))

    def get_comments(self, resource_id: str) -> List[Dict[str, Any]]:
        """Retrieve all comments for a resource"""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM social_comments WHERE resource_id = ?", (resource_id,))
            return [dict(row) for row in cursor.fetchall()]

# Global Instance
_kb = None

def get_knowledge_base() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
