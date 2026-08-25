"""Orchestrates the whole run: for each configured unit, upload its native
PDF(s), walk it through a multi-turn chat (agenda -> one detailed pass per
section -> glossary), and assemble one master Markdown document.

The section-by-section loop is the fix for the original problem: asking
Gemini for one giant summary in a single turn makes it compress/skip
material. Asking for one dedicated, generously-budgeted response per section
- while keeping the whole unit in one chat session for continuity - gets
exhaustive coverage without needing several manual chat sessions.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import List, Optional

from . import gemini_client, prompts
from .config import CourseConfig, Unit
from .course_context import build_course_context

# How many agenda topics go into one Gemini call. Batching cuts wall-clock
# time and API call count roughly by this factor versus one call per topic,
# at the cost of a larger per-call output budget.
SECTION_BATCH_SIZE = 4
SECTION_MAX_OUTPUT_TOKENS = 24000


def _parse_agenda(agenda_text: str) -> List[str]:
    titles = []
    for line in agenda_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # strip leading list markers like "1.", "1)", "-", "*"
        cleaned = re.sub(r"^(\d+[\.\)]|[-*])\s*", "", line).strip()
        cleaned = re.sub(r"\*+", "", cleaned).strip()
        if cleaned:
            titles.append(cleaned)
    return titles if len(titles) >= 2 else ["Full deck"]


def _unit_cache_path(course: CourseConfig, unit: Unit) -> Path:
    return course.output_dir / "cache" / f"unit_{unit.cache_key(course)}.json"


def _exam_fingerprint(course: CourseConfig) -> str:
    """Identity of the past-exam material + the prompt that consumes it, so
    swapping in a new exam paper regenerates the practice pages."""
    h = hashlib.sha256()
    h.update(prompts.EXAM_PROMPT_VERSION.encode("utf-8"))
    for p in course.exam_paths():
        h.update(p.name.encode("utf-8"))
        h.update(str(p.stat().st_mtime_ns).encode("utf-8"))
    return h.hexdigest()[:12]


def _exam_cache_path(course: CourseConfig, unit: Unit, exam_fp: str) -> Path:
    """Exam pages are cached apart from the chapter summary they attach to,
    so turning --with-tests on later doesn't throw away the summaries."""
    return course.output_dir / "cache" / f"exam_{unit.cache_key(course)}_{exam_fp}.json"


def generate_exam_block(client, course: CourseConfig, unit: Unit, unit_markdown: str,
                        exam_files: List, exam_fp: str, use_cache: bool = True) -> Optional[str]:
    """Asks for one past-exam question covering this unit, plus a worked
    solution. Returns the Markdown block, or None when the exams contain
    nothing relevant to this unit.

    Deliberately a fresh one-shot chat rather than a turn inside the unit's
    summarising chat: that way it works identically whether the summary was
    just generated or loaded from cache, and the exam PDFs are sent exactly
    once per unit instead of riding along in every turn of a long session.
    """
    cache_path = _exam_cache_path(course, unit, exam_fp)
    if use_cache and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))["markdown"]
        print(f"  Using cached exam question for '{unit.name}'")
        return cached or None

    print(f"  Looking for a past-exam question on '{unit.name}'...")
    system_instruction = prompts.SYSTEM_INSTRUCTION.format(course_name=course.course_name)
    chat = gemini_client.start_chat(
        client, course.model, system_instruction, max_output_tokens=SECTION_MAX_OUTPUT_TOKENS
    )
    prompt = prompts.EXAM_PROMPT.format(
        unit_name=unit.name,
        unit_summary=unit_markdown,
        no_question_marker=prompts.EXAM_NO_QUESTION_MARKER,
    )
    answer = gemini_client.send(chat, [prompt, *exam_files]).strip()

    if prompts.EXAM_NO_QUESTION_MARKER in answer:
        print(f"  No past-exam question matches '{unit.name}' - skipping its practice page")
        block = ""
    else:
        block = "## שאלה ממבחנים קודמים\n\n" + answer

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"markdown": block}, ensure_ascii=False, indent=2), encoding="utf-8")
    return block or None


def process_unit(client, course: CourseConfig, unit: Unit, digest: str, use_cache: bool = True) -> str:
    """Returns the assembled Markdown for one unit (agenda + sections + glossary)."""
    cache_path = _unit_cache_path(course, unit)
    if use_cache and cache_path.exists():
        print(f"[pipeline] Using cached result for unit '{unit.name}'")
        return json.loads(cache_path.read_text(encoding="utf-8"))["markdown"]

    print(f"[pipeline] Processing unit '{unit.name}'")
    source_paths = [p for p in unit.source_paths(course) if p.exists()]
    missing = [p for p in unit.source_paths(course) if not p.exists()]
    for m in missing:
        print(f"  [warn] configured file not found, skipping: {m}")
    if not source_paths:
        raise FileNotFoundError(f"No source PDFs found for unit '{unit.name}'")

    uploaded_files = []
    for p in source_paths:
        print(f"  Uploading {p.name} to Gemini...")
        uploaded_files.append(gemini_client.upload_pdf(client, p))

    system_instruction = prompts.SYSTEM_INSTRUCTION.format(course_name=course.course_name)
    chat = gemini_client.start_chat(
        client, course.model, system_instruction, max_output_tokens=SECTION_MAX_OUTPUT_TOKENS
    )

    agenda_prompt = prompts.AGENDA_PROMPT.format(digest=digest, unit_name=unit.name)
    agenda_text = gemini_client.send(chat, [agenda_prompt, *uploaded_files])
    section_titles = _parse_agenda(agenda_text)
    print(f"  Agenda: {len(section_titles)} section(s)")

    print("  Planning content boundaries...")
    gemini_client.send(chat, [prompts.OUTLINE_PROMPT])

    numbered_titles = list(enumerate(section_titles, start=1))
    section_bodies = []
    for batch_start in range(0, len(numbered_titles), SECTION_BATCH_SIZE):
        batch = numbered_titles[batch_start:batch_start + SECTION_BATCH_SIZE]
        first_i, last_i = batch[0][0], batch[-1][0]
        print(f"  Writing topics {first_i}-{last_i}/{len(numbered_titles)}: "
              f"{', '.join(t for _, t in batch)}")
        topic_list = "\n".join(f"- Topic {i}: {t}" for i, t in batch)
        body = gemini_client.send(chat, [prompts.SECTION_PROMPT.format(n=len(batch), topic_list=topic_list)])
        section_bodies.append(body)

    print("  Writing glossary...")
    glossary_text = gemini_client.send(chat, [prompts.GLOSSARY_PROMPT])

    markdown = "\n\n".join([
        f"# {unit.name}",
        "## סדר יום\n\n" + agenda_text,
        *section_bodies,
        "## מושגי מפתח\n\n" + glossary_text,
    ])

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"markdown": markdown}, ensure_ascii=False, indent=2), encoding="utf-8")

    return markdown


def run(course: CourseConfig, use_cache: bool = True, force_context: bool = False,
        with_tests: bool = False) -> List[str]:
    """Runs every unit and returns a list of top-level Markdown sections
    (cover page first, then one per unit) - kept separate so the PDF builder
    can insert a page break between each one.

    With `with_tests`, each unit also gets a past-exam practice question
    appended, provided the course has an exams folder to draw from."""
    client = gemini_client.make_client()
    digest = build_course_context(course, force=force_context)

    exam_files, exam_fp = [], ""
    if with_tests:
        exam_paths = course.exam_paths()
        if not exam_paths:
            print("[pipeline] --with-tests was passed but no past-exam PDFs were found "
                  f"({course.exams_dir or 'no exams folder'}) - continuing without exam pages.")
        else:
            exam_fp = _exam_fingerprint(course)
            print(f"[pipeline] Uploading {len(exam_paths)} past-exam PDF(s) from {course.exams_dir.name}...")
            for p in exam_paths:
                print(f"  Uploading {p.name}...")
                exam_files.append(gemini_client.upload_pdf(client, p))

    unit_markdowns = []
    for unit in course.units:
        markdown = process_unit(client, course, unit, digest, use_cache=use_cache)
        if exam_files:
            exam_block = generate_exam_block(
                client, course, unit, markdown, exam_files, exam_fp, use_cache=use_cache
            )
            if exam_block:
                markdown = markdown + "\n\n" + exam_block
        unit_markdowns.append(markdown)

    cover = f"# {course.course_name}\n## סיכום לקראת בחינה\n\n" + "## תוכן עניינים\n\n" + "\n".join(
        f"@@TOC@@ {i}|{u.name}" for i, u in enumerate(course.units, start=1)
    )

    return [cover, *unit_markdowns]
