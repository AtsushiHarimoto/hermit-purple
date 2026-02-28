"""
Hermit Purple utils 單元測試

用途：驗證 safe_parse_json、_safe_float、_safe_int、run_async
依賴：unittest（不需要網路）
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import safe_parse_json, _safe_float, _safe_int, run_async


# ── safe_parse_json Tests ────────────────────────────────────────────

class TestSafeParseJson(unittest.TestCase):
    """測試魯棒的 JSON 解析器"""

    def test_valid_json_object(self):
        """標準 JSON 物件應正確解析"""
        result = safe_parse_json('{"key": "value", "num": 42}')
        self.assertIsInstance(result, dict)
        self.assertEqual(result["key"], "value")
        self.assertEqual(result["num"], 42)

    def test_valid_json_array(self):
        """標準 JSON 陣列應正確解析"""
        result = safe_parse_json('[1, 2, 3]')
        self.assertIsInstance(result, list)
        self.assertEqual(result, [1, 2, 3])

    def test_markdown_code_block_json(self):
        """Markdown ```json 代碼塊應正確解析"""
        text = '```json\n{"name": "test"}\n```'
        result = safe_parse_json(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["name"], "test")

    def test_markdown_code_block_no_language(self):
        """Markdown ``` 代碼塊（無語言標記）應正確解析"""
        text = '```\n[1, 2, 3]\n```'
        result = safe_parse_json(text)
        self.assertIsInstance(result, list)
        self.assertEqual(result, [1, 2, 3])

    def test_json_with_preamble(self):
        """帶前導文字的 JSON 應正確解析"""
        text = 'Here is the result:\n{"status": "ok"}'
        result = safe_parse_json(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["status"], "ok")

    def test_empty_input_returns_none(self):
        """空字串應回傳 None"""
        self.assertIsNone(safe_parse_json(""))

    def test_none_like_empty(self):
        """None 也應回傳 None（函數開頭 if not text）"""
        self.assertIsNone(safe_parse_json(None))

    def test_malformed_json_returns_none(self):
        """完全無效的文字應回傳 None"""
        result = safe_parse_json("This is not JSON at all, no brackets here.")
        self.assertIsNone(result)

    def test_nested_json(self):
        """嵌套 JSON 應正確解析"""
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = safe_parse_json(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["outer"]["inner"], [1, 2, 3])

    def test_json_with_trailing_text(self):
        """JSON 後方有多餘文字不應影響解析"""
        text = '{"key": "val"}\n\nSome trailing text'
        result = safe_parse_json(text)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["key"], "val")

    def test_array_in_markdown(self):
        """Markdown 代碼塊中的陣列"""
        text = '```json\n[{"id": 1}, {"id": 2}]\n```'
        result = safe_parse_json(text)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    def test_whitespace_only_returns_none(self):
        """只有空白的字串應回傳 None"""
        result = safe_parse_json("   \n\t  ")
        self.assertIsNone(result)

    def test_unclosed_markdown_block(self):
        """未閉合的 Markdown 代碼塊應儘力解析"""
        text = '```json\n{"name": "unclosed"}'
        result = safe_parse_json(text)
        # 應嘗試解析（Strategy 1 會截取到末尾）
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "unclosed")

    def test_multiple_json_objects_returns_first(self):
        """多個 JSON 物件時應解析第一個"""
        text = 'text before {"first": 1} {"second": 2}'
        result = safe_parse_json(text)
        self.assertIsNotNone(result)
        # dirtyjson may parse from the first { onwards
        self.assertIn("first", result)


# ── _safe_float Tests ────────────────────────────────────────────────

class TestSafeFloat(unittest.TestCase):
    """測試安全 float 解析"""

    def test_valid_int(self):
        """整數應正確轉為 float"""
        self.assertEqual(_safe_float(42), 42.0)

    def test_valid_float(self):
        """浮點數應正確返回"""
        self.assertAlmostEqual(_safe_float(3.14), 3.14)

    def test_valid_string_number(self):
        """數字字串應正確解析"""
        self.assertAlmostEqual(_safe_float("2.5"), 2.5)

    def test_invalid_string_returns_default(self):
        """無效字串應返回預設值"""
        self.assertEqual(_safe_float("not-a-number"), 1.0)

    def test_none_returns_default(self):
        """None 應返回預設值"""
        self.assertEqual(_safe_float(None), 1.0)

    def test_custom_default(self):
        """自訂預設值應被使用"""
        self.assertEqual(_safe_float("bad", default=0.0), 0.0)

    def test_zero(self):
        """0 應正確返回 0.0"""
        self.assertEqual(_safe_float(0), 0.0)

    def test_negative(self):
        """負數應正確返回"""
        self.assertEqual(_safe_float(-1.5), -1.5)

    def test_empty_string_returns_default(self):
        """空字串應返回預設值"""
        self.assertEqual(_safe_float(""), 1.0)

    def test_boolean_true(self):
        """bool True 可以被 float() 接受（Python 特性: float(True) == 1.0）"""
        self.assertEqual(_safe_float(True), 1.0)


# ── _safe_int Tests ──────────────────────────────────────────────────

class TestSafeInt(unittest.TestCase):
    """測試安全 int 解析"""

    def test_valid_int(self):
        """整數應正確返回"""
        self.assertEqual(_safe_int(42), 42)

    def test_valid_string_int(self):
        """整數字串應正確解析"""
        self.assertEqual(_safe_int("100"), 100)

    def test_invalid_string_returns_default(self):
        """無效字串應返回預設值"""
        self.assertEqual(_safe_int("abc"), 1)

    def test_none_returns_default(self):
        """None 應返回預設值"""
        self.assertEqual(_safe_int(None), 1)

    def test_custom_default(self):
        """自訂預設值應被使用"""
        self.assertEqual(_safe_int("bad", default=0), 0)

    def test_zero(self):
        """0 應正確返回"""
        self.assertEqual(_safe_int(0), 0)

    def test_negative(self):
        """負整數應正確返回"""
        self.assertEqual(_safe_int(-5), -5)

    def test_float_truncated(self):
        """浮點數應截斷為整數"""
        self.assertEqual(_safe_int(3.9), 3)

    def test_float_string_returns_default(self):
        """浮點字串（如 '3.14'）應返回預設值（int('3.14') 會失敗）"""
        self.assertEqual(_safe_int("3.14"), 1)

    def test_empty_string_returns_default(self):
        """空字串應返回預設值"""
        self.assertEqual(_safe_int(""), 1)


# ── run_async Tests ──────────────────────────────────────────────────

class TestRunAsync(unittest.TestCase):
    """測試 run_async 工具函數"""

    def test_runs_coroutine_without_loop(self):
        """在沒有 event loop 的環境中應正確執行"""
        async def sample():
            return 42

        result = run_async(sample())
        self.assertEqual(result, 42)

    def test_returns_value(self):
        """應正確返回 coroutine 的結果"""
        async def greet(name):
            return f"Hello, {name}"

        result = run_async(greet("World"))
        self.assertEqual(result, "Hello, World")

    def test_propagates_exception(self):
        """coroutine 中的異常應被正確傳播"""
        async def fail():
            raise ValueError("test error")

        with self.assertRaises(ValueError) as ctx:
            run_async(fail())
        self.assertIn("test error", str(ctx.exception))

    def test_async_with_await(self):
        """帶有 await 的 coroutine 應正確執行"""
        async def multi_step():
            a = await asyncio.coroutine(lambda: 1)() if hasattr(asyncio, 'coroutine') else 1
            return a + 1

        # Use a simpler approach
        async def add():
            await asyncio.sleep(0)
            return 10 + 20

        result = run_async(add())
        self.assertEqual(result, 30)


if __name__ == "__main__":
    unittest.main()
