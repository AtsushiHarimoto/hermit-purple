"""Unit tests for discover_trending_keywords merge / dedup / normalization."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.mcp_server as mcp_mod  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

_next_id = 0

def _fake_resource(tags: list[str], ring: str = "assess", confidence: float = 0.5):
    """Build a mock Resource ORM object."""
    global _next_id
    _next_id += 1
    return SimpleNamespace(
        id=_next_id,
        tags=tags,
        metrics={"ring": ring, "confidence": confidence},
        scraped_at=datetime.now(timezone.utc),
        verification_status="verified",
    )


def _make_env(**kw):
    defaults = {
        "ai_base_url": "http://test/v1",
        "ai_api_key": "sk-test",
        "ai_model": "test-model",
        "gemini_api_key": "",
        "gemini_official_base_url": "",
        "gemini_official_model": "",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_chat_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _FakeQuery:
    """Minimal chained-filter mock that returns preset resources.

    Any chaining method (filter, join, limit, order_by, etc.) returns self.
    Terminal methods first() and all() return data.
    """

    def __init__(self, resources):
        self._resources = resources

    def __getattr__(self, name):
        """Return a chainable no-op for any unknown attribute (filter, join, limit, ...)."""
        return lambda *_a, **_kw: self

    def first(self):
        return self._resources[0] if self._resources else None

    def all(self):
        return self._resources


class _FakeDB:
    def __init__(self, resources):
        self._q = _FakeQuery(resources)

    def query(self, _model):
        return self._q

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class _NoCategoryDB:
    """DB mock with no ResourceCategory links. Supports add/commit for backfill tests."""

    def __init__(self, resources):
        self._resources = resources
        self.added = []

    def query(self, model):
        from src.db.models import ResourceCategory as RC
        # Use `is` — `==` triggers SQLAlchemy column comparison and fails
        if model is RC.id or model is RC.resource_id:
            return _FakeQuery([])
        return _FakeQuery(self._resources)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ── tests ────────────────────────────────────────────────────────────────────

@patch.object(mcp_mod, "init_db")
@patch.object(mcp_mod, "get_db")
class TestDiscoverMergeLogic(unittest.TestCase):
    """Tests that exercise merge, dedup, normalization and top_k without real DB/AI."""

    # ----- 1. Dedup: AI entry wins over same local keyword -----
    @patch.object(mcp_mod, "get_env")
    @patch("src.mcp_server.OpenAI")
    def test_dedup_ai_wins_over_local(self, MockOpenAI, mock_env, mock_get_db, mock_init):
        """When the same keyword appears in AI and local, AI entry is kept."""
        # AI returns "React" with score 0.9
        ai_json = json.dumps([{"keyword": "React", "score": 0.9, "frequency": 5, "source": "ai_search"}])
        client_inst = MagicMock()
        client_inst.chat.completions.create.return_value = _make_chat_response(ai_json)
        MockOpenAI.return_value = client_inst
        mock_env.return_value = _make_env()

        # Local DB also has "react" tag with high accumulated score
        resources = [_fake_resource(["react"], ring="adopt", confidence=0.9)] * 10
        mock_get_db.return_value = _FakeDB(resources)

        raw = mcp_mod.discover_trending_keywords(
            category="frontend", seed_keywords="", days=30, top_k=30, use_ai=True,
        )
        result = json.loads(raw)

        react_items = [r for r in result if r["keyword"].lower() == "react"]
        self.assertEqual(len(react_items), 1, "Duplicate keyword should be deduped")
        self.assertEqual(react_items[0]["source"], "ai_search", "AI entry should win")

    # ----- 1b. Dedup within AI: keep highest score when model repeats a keyword -----
    @patch.object(mcp_mod, "get_env")
    @patch("src.mcp_server.OpenAI")
    def test_dedup_within_ai_keeps_highest_score(self, MockOpenAI, mock_env, mock_get_db, mock_init):
        """When AI returns the same keyword multiple times, keep the highest-score entry."""
        ai_json = json.dumps([
            {"keyword": "React", "score": 0.1, "frequency": 1, "source": "ai_search"},
            {"keyword": "React", "score": 0.95, "frequency": 10, "source": "ai_search"},
            {"keyword": "react", "score": 0.5, "frequency": 3, "source": "ai_search"},
        ])
        client_inst = MagicMock()
        client_inst.chat.completions.create.return_value = _make_chat_response(ai_json)
        MockOpenAI.return_value = client_inst
        mock_env.return_value = _make_env()

        mock_get_db.return_value = _FakeDB([])

        raw = mcp_mod.discover_trending_keywords(
            category="frontend", seed_keywords="", days=30, top_k=30, use_ai=True,
        )
        result = json.loads(raw)

        react_items = [r for r in result if r["keyword"].lower() == "react"]
        self.assertEqual(len(react_items), 1, "AI duplicates should be deduped to one")
        # After normalization of a single item the score becomes 1.0,
        # but the important thing is it came from the 0.95 entry (frequency=10)
        self.assertEqual(react_items[0]["frequency"], 10, "Should keep the highest-score entry")

    # ----- 2. Score normalization: both sources end up in 0-1 -----
    @patch.object(mcp_mod, "get_env")
    @patch("src.mcp_server.OpenAI")
    def test_scores_normalized_to_0_1(self, MockOpenAI, mock_env, mock_get_db, mock_init):
        """After normalization all scores should be in [0, 1]."""
        ai_json = json.dumps([
            {"keyword": "Svelte", "score": 0.3, "frequency": 2, "source": "ai_search"},
            {"keyword": "Solid", "score": 0.9, "frequency": 8, "source": "ai_search"},
        ])
        client_inst = MagicMock()
        client_inst.chat.completions.create.return_value = _make_chat_response(ai_json)
        MockOpenAI.return_value = client_inst
        mock_env.return_value = _make_env()

        # Local DB tags with high accumulated scores (>>1)
        resources = [_fake_resource(["vue"], ring="adopt", confidence=0.95)] * 20
        mock_get_db.return_value = _FakeDB(resources)

        raw = mcp_mod.discover_trending_keywords(
            category="frontend", seed_keywords="", days=30, top_k=30, use_ai=True,
        )
        result = json.loads(raw)

        for item in result:
            self.assertGreaterEqual(item["score"], 0.0, f"{item['keyword']} score < 0")
            self.assertLessEqual(item["score"], 1.0, f"{item['keyword']} score > 1")

    # ----- 3. top_k truncation -----
    def test_top_k_truncation(self, mock_get_db, mock_init):
        """Result length should not exceed top_k (AI disabled, local only)."""
        resources = [_fake_resource([f"tag{i}"]) for i in range(20)]
        mock_get_db.return_value = _FakeDB(resources)

        raw = mcp_mod.discover_trending_keywords(
            category="", seed_keywords="", days=30, top_k=5, use_ai=False,
        )
        result = json.loads(raw)
        self.assertLessEqual(len(result), 5)

    # ----- 4. Seed keywords excluded -----
    def test_seed_keywords_excluded(self, mock_get_db, mock_init):
        """Keywords matching seed_keywords should be filtered out."""
        resources = [_fake_resource(["Python", "Rust", "Go"])]
        mock_get_db.return_value = _FakeDB(resources)

        raw = mcp_mod.discover_trending_keywords(
            category="", seed_keywords="python,go", days=30, top_k=30, use_ai=False,
        )
        result = json.loads(raw)
        kws = {r["keyword"].lower() for r in result}
        self.assertNotIn("python", kws)
        self.assertNotIn("go", kws)
        self.assertIn("rust", kws)

    # ----- 5. Local-only when use_ai=False -----
    def test_local_only_mode(self, mock_get_db, mock_init):
        """With use_ai=False, results should only contain local_db source."""
        resources = [_fake_resource(["Alpha", "Beta"])]
        mock_get_db.return_value = _FakeDB(resources)

        raw = mcp_mod.discover_trending_keywords(
            category="test", seed_keywords="", days=30, top_k=30, use_ai=False,
        )
        result = json.loads(raw)
        for item in result:
            self.assertEqual(item["source"], "local_db")

    # ----- 6. Empty results -----
    def test_empty_results(self, mock_get_db, mock_init):
        """No resources should yield empty list, not crash."""
        mock_get_db.return_value = _FakeDB([])

        raw = mcp_mod.discover_trending_keywords(
            category="", seed_keywords="", days=30, top_k=30, use_ai=False,
        )
        result = json.loads(raw)
        self.assertEqual(result, [])

    # ----- 7. Fallback when no resource_categories links AND no seeds -----
    def test_fallback_when_no_category_links_no_seeds(self, mock_get_db, mock_init):
        """With category set but no ResourceCategory links and no seeds, should fallback to unfiltered."""
        resources = [_fake_resource(["Gamma", "Delta"])]
        mock_get_db.return_value = _NoCategoryDB(resources)

        raw = mcp_mod.discover_trending_keywords(
            category="newcat", seed_keywords="", days=30, top_k=30, use_ai=False,
        )
        result = json.loads(raw)
        kws = {r["keyword"].lower() for r in result}
        self.assertIn("gamma", kws, "Fallback should include unfiltered resources")
        self.assertIn("delta", kws, "Fallback should include unfiltered resources")

    # ----- 8. Backfill: seed tags filter resources when no category links -----
    def test_backfill_filters_by_seed_tags(self, mock_get_db, mock_init):
        """With category + seeds but no links, only seed-matching resources are used."""
        resources = [
            _fake_resource(["react", "hooks"], ring="adopt", confidence=0.9),
            _fake_resource(["python", "django"], ring="adopt", confidence=0.9),
            _fake_resource(["react", "typescript"], ring="trial", confidence=0.8),
        ]

        db = _NoCategoryDB(resources)
        mock_get_db.return_value = db

        raw = mcp_mod.discover_trending_keywords(
            category="frontend", seed_keywords="react", days=30, top_k=30, use_ai=False,
        )
        result = json.loads(raw)
        kws = {r["keyword"].lower() for r in result}
        # "react" is a seed keyword — excluded from results
        self.assertNotIn("react", kws, "Seed keyword should be excluded")
        # "hooks" and "typescript" should appear (from react-tagged resources)
        self.assertIn("hooks", kws, "Tags from seed-matching resources should appear")
        self.assertIn("typescript", kws, "Tags from seed-matching resources should appear")
        # "python" and "django" should NOT appear (resource has no seed tag overlap)
        self.assertNotIn("python", kws, "Non-matching resource tags should be excluded")
        self.assertNotIn("django", kws, "Non-matching resource tags should be excluded")


if __name__ == "__main__":
    unittest.main()
