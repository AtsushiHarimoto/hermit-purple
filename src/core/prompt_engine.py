"""
Hermit Purple Core: Prompt Permutator (Anti-Fingerprinting)
"""
import random
from typing import List

# Enhanced Persona Library (English + Traditional Chinese)
PERSONAS = [
    "You are a cynical Senior Software Architect who hates hype.",
    "You are an energetic VC Scout looking for the next unicorn.",
    "You are a bored Open Source Maintainer filtering spam.",
    "You are a meticulous Data Scientist focused on facts only.",
    "You are a chill Indie Hacker looking for cool tools.",
    "Act as a CTO evaluating tech stacks for a startup.",
    "Role: Tech Journalist writing a 'Week in Review' column.",
    "Imagine you are a DevRel looking for projects to sponsor.",
    "你是一位嚴謹的資深軟體架構師，厭惡誇大宣傳。",
    "你是一位充滿熱情的創投分析師，尋找下一個獨角獸。",
    "你是一位務實的開源維護者，專注過濾低質量內容。",
    "你是一位謹慎的數據科學家，只相信事實與數據。",
]

# Varied Intent Phrasings
TASK_VARIANTS = [
    "List top trending AI tools related to",
    "Identify emerging software in the AI space regarding",
    "Scan the horizon for new tech specifically for",
    "I need a distinct list of fresh projects concerning",
    "Give me the lowdown on what's hot in",
    "Find me the most starred/discussed repos about",
    "Extract intelligence on recent developments in",
]

# Output Format Directives (Structural Noise)
FORMAT_STYLES = [
    "輸出格式：嚴格 JSON 陣列。",
    "僅輸出格式正確的 JSON 陣列。",
    "必須輸出嚴格 JSON，禁止前言。",
    "請輸出原始 JSON 列表格式。",
    "請輸出可解析的 JSON 陣列。",
]


class PromptPermutator:
    """
    Generates dynamic prompts to avoid repetitive signatures.
    """

    @staticmethod
    def permutate(base_intent: str, keywords: List[str], days: int = 30) -> str:
        """
        Constructs a unique prompt version for the same intent.
        Features:
        - Persona Rotation
        - Phrasing Jitter
        - Keyword Shuffling
        - Instruction Reordering
        """
        persona = random.choice(PERSONAS)
        task_phrasing = random.choice(TASK_VARIANTS)
        format_instr = random.choice(FORMAT_STYLES)

        # 1. Keyword Jitter
        shuffled_keywords = keywords.copy()
        random.shuffle(shuffled_keywords)
        # Randomly choose separator
        separator = random.choice([", ", " | ", " and ", " + "])
        kw_str = separator.join(shuffled_keywords)

        # 2. Component Blocks
        blocks = [
            f"{persona}",
            base_intent,  # caller's original task intent
            f"{task_phrasing}: {kw_str}.",
            f"Focus on NEW tools (released/updated in last {days} days).",
            random.choice(["", "Be precise.", "No marketing fluff."]),
            format_instr,
        ]

        # 3. Structural Randomization (Shuffle the order of instructions slightly)
        # Keep persona first usually, but shuffle the middle instructions
        middle = blocks[1:-1]
        random.shuffle(middle)
        final_structure = [blocks[0]] + middle + [blocks[-1]]

        raw_prompt = "\n".join(final_structure)
        raw_prompt += "\n輸出語言：繁體中文。"

        return raw_prompt.strip()


_permutator = PromptPermutator()


def get_prompt_engine() -> PromptPermutator:
    return _permutator
