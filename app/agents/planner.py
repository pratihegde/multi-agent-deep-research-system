from __future__ import annotations

import textwrap

from app.config import HARD_MAX_SUBQUESTIONS, MAX_QUERIES_PER_SUBQUESTION, MAX_SUBQUESTIONS, query_intent
from app.models import Plan, SubQuestion
from app.services.llm import call_openai_typed


PLANNER_SYSTEM = (
    "You are a research planning agent. Return ONLY valid JSON matching the schema. "
    "Generate a practical plan with 3-6 sub-questions, each with 2-4 search queries."
)


def planner_prompt(query: str, history: list[dict[str, str]]) -> str:
    recent_history = _summarize_history(history)
    return textwrap.dedent(
        f"""
        User query:
        {query}

        Recent thread history:
        {recent_history or "- (none)"}

        Requirements:
        - Output keys: sub_questions, assumptions.
        - sub_questions: 3 to 6 items (target 4).
        - Each sub_question has: id, question, priority, search_queries.
        - id format: sq1, sq2, ...
        - priority is unique integer (1 = highest).
        - search_queries: 2 to 4 short focused web queries.
        - If user query is ambiguous, add explicit assumptions.
        """
    ).strip()


def _summarize_history(history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    selected: list[str] = []
    # Keep at most two latest user messages and one assistant message to avoid token bloat.
    user_count = 0
    assistant_count = 0
    for item in reversed(history):
        role = item.get("role", "user")
        content = " ".join((item.get("content") or "").split())
        if not content:
            continue
        if role == "user" and user_count < 2:
            user_count += 1
            selected.append(f"- user: {content[:220]}")
        elif role == "assistant" and assistant_count < 1:
            assistant_count += 1
            selected.append(f"- assistant: {content[:220]}")
        if user_count >= 2 and assistant_count >= 1:
            break
    return "\n".join(reversed(selected))


def _fallback_plan(query: str) -> Plan:
    base = [
        SubQuestion(
            id="sq1",
            question=f"What is the current landscape relevant to: {query}?",
            priority=1,
            search_queries=[f"{query} overview", f"{query} latest data"],
        ),
        SubQuestion(
            id="sq2",
            question=f"What are the main risks and downsides for: {query}?",
            priority=2,
            search_queries=[f"{query} risks", f"{query} challenges evidence"],
        ),
        SubQuestion(
            id="sq3",
            question=f"What opportunities and best practices exist for: {query}?",
            priority=3,
            search_queries=[f"{query} opportunities", f"{query} best practices"],
        ),
    ]
    return Plan(sub_questions=base, assumptions=["Fallback plan generated due to parser/model failure."])


def _historical_plan(query: str) -> Plan:
    # Deterministic historical decomposition improves reliability for timeline/community-history questions.
    sub_questions = [
        SubQuestion(
            id="sq1",
            question="What are the major historical periods and timeline for this topic?",
            priority=1,
            search_queries=[
                f"{query} timeline",
                f"{query} historical periods wikipedia",
            ],
        ),
        SubQuestion(
            id="sq2",
            question="Which dynasties, rulers, or institutions shaped this history?",
            priority=2,
            search_queries=[
                f"{query} dynasties rulers",
                f"{query} political history sources",
            ],
        ),
        SubQuestion(
            id="sq3",
            question="What cultural, linguistic, and social shifts occurred over time?",
            priority=3,
            search_queries=[
                f"{query} cultural history",
                f"{query} social linguistic changes",
            ],
        ),
        SubQuestion(
            id="sq4",
            question="What are key modern interpretations, debates, and data gaps?",
            priority=4,
            search_queries=[
                f"{query} historiography debate",
                f"{query} research gaps",
            ],
        ),
    ]
    return Plan(
        sub_questions=sub_questions,
        assumptions=["Historical template plan used for stronger timeline coverage."],
    )


def _ensure_two_queries(queries: list[str], question: str) -> list[str]:
    deduped = [q.strip() for q in queries if q and q.strip()]
    if not deduped:
        deduped = [question]
    while len(deduped) < MAX_QUERIES_PER_SUBQUESTION:
        deduped.append(f"{question} latest evidence")
    return deduped[:MAX_QUERIES_PER_SUBQUESTION]


async def run_planner(query: str, history: list[dict[str, str]], prior_context: str = "") -> Plan:
    if query_intent(query) == "historical":
        return _historical_plan(query)

    history_with_context = list(history)
    if prior_context:
        history_with_context.append(
            {
                "role": "assistant",
                "content": f"Prior context summary: {prior_context[:280]}",
            }
        )
    prompt = planner_prompt(query=query, history=history_with_context)
    try:
        plan = await call_openai_typed(
            system_prompt=PLANNER_SYSTEM,
            user_prompt=prompt,
            schema=Plan,
        )
        # Normalize id/priority ordering for safety.
        sorted_subqs = sorted(plan.sub_questions, key=lambda sq: sq.priority)
        normalized = [
            SubQuestion(
                id=f"sq{idx + 1}",
                question=sq.question,
                priority=idx + 1,
                search_queries=_ensure_two_queries(sq.search_queries, sq.question),
            )
            for idx, sq in enumerate(sorted_subqs[:HARD_MAX_SUBQUESTIONS])
        ]
        if len(normalized) < 3:
            return _fallback_plan(query)
        normalized = normalized[:MAX_SUBQUESTIONS]
        normalized = [
            SubQuestion(
                id=item.id,
                question=item.question,
                priority=item.priority,
                search_queries=_ensure_two_queries(item.search_queries, item.question),
            )
            for item in normalized
        ]
        return Plan(sub_questions=normalized, assumptions=plan.assumptions)
    except Exception:
        return _fallback_plan(query)
