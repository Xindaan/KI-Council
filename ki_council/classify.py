"""Sort prompts into segments so the verdict can be given per kind of work.

A single win-or-tie rate over a mixed prompt set is a weighted average, and
averages hide exactly what matters here: a cheap model that wins every
rephrasing task and collapses on every architecture question can still clear a
90% bar. The user would then downgrade and lose precisely the work they kept
the expensive model for.

So each prompt is labelled before the run and the report breaks the rates down
by label. The taxonomy is fixed rather than free-form: labels have to be
comparable across runs to be aggregated, and a model inventing its own
categories produces a report that cannot be read twice.

Classification costs one cheap call per prompt (not per pair) and is a source
of error in its own right -- a misfiled prompt lands in the wrong segment. An
unparseable answer therefore becomes UNCLASSIFIED rather than a guess.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

from ki_council.promptsets import PromptItem

logger = logging.getLogger(__name__)

CATEGORIES = ("coding", "analysis", "writing", "factual", "other")
DIFFICULTIES = ("easy", "medium", "hard")
UNCLASSIFIED = "unclassified"

CLASSIFY_MAX_TOKENS = 32  # a label, not an essay

CLASSIFY_TEMPLATE = """Classify the user prompt below into exactly one category and one difficulty.

Categories:
- coding: writing, debugging, reviewing or explaining code
- analysis: reasoning, planning, comparing options, multi-step problem solving
- writing: drafting, rewriting, summarizing or translating prose
- factual: a lookup or definition with a short, checkable answer
- other: anything that fits none of the above

Difficulty:
- easy: a competent small model would handle it
- medium: needs care, but is not deep work
- hard: needs strong reasoning, domain knowledge or long-range consistency

[User prompt]
{prompt}

Answer with exactly one line, nothing else: category/difficulty
Example: coding/hard"""


def build_classify_prompt(text: str) -> str:
    return CLASSIFY_TEMPLATE.format(prompt=text)


def parse_classification(output: str) -> Tuple[str, str]:
    """Read "category/difficulty" out of the model's answer.

    Anything unexpected becomes UNCLASSIFIED. Guessing a label would quietly
    move a prompt into the wrong segment, and a wrong segment is worse than a
    visibly missing one.
    """
    if not isinstance(output, str):
        return (UNCLASSIFIED, UNCLASSIFIED)
    for line in reversed(output.strip().splitlines()):
        match = re.search(r"([a-z]+)\s*/\s*([a-z]+)", line.strip().lower())
        if not match:
            continue
        category, difficulty = match.group(1), match.group(2)
        if category in CATEGORIES and difficulty in DIFFICULTIES:
            return (category, difficulty)
    logger.warning(f"Could not parse classification: {output!r}")
    return (UNCLASSIFIED, UNCLASSIFIED)


def classify_prompts(
    prompts: List[PromptItem],
    classify_fn: Callable[[str], str],
    workers: int = 4,
) -> Dict[str, Tuple[str, str]]:
    """Label every prompt. Returns {prompt_id: (category, difficulty)}.

    A failing classifier must not take the run down with it: the prompt simply
    stays unclassified and shows up in that segment.
    """
    def classify(item: PromptItem) -> Tuple[str, Tuple[str, str]]:
        try:
            return (item.id, parse_classification(classify_fn(build_classify_prompt(item.text))))
        except Exception as exc:
            logger.warning(f"Classifier failed on prompt '{item.id}': {exc}")
            return (item.id, (UNCLASSIFIED, UNCLASSIFIED))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(classify, prompts))


def segment_labels(
    classifications: Optional[Dict[str, Tuple[str, str]]], by: str = "category"
) -> Dict[str, str]:
    """Flatten the classification into one label per prompt, for grouping."""
    if not classifications:
        return {}
    index = 0 if by == "category" else 1
    return {
        prompt_id: labels[index] for prompt_id, labels in classifications.items()
    }
