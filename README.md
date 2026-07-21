# exam_prep_summarizer

Turns a course's lecture + exercise PDFs into one exam-study PDF, using the
Gemini API. Works for any course with a similar folder layout - not just
Data Science.

## How it works

For each **unit** (one lecture, or a group of lectures on one subject) it:

1. Uploads the unit's actual PDF(s) to Gemini natively (so slide layout,
   diagrams, and visuals are read directly - no lossy text conversion).
2. Sends along a lightweight text digest of *every* lecture/exercise in the
   course (extracted locally, cheaply) so Gemini has full-course context for
   cross-referencing, without re-uploading everything as images each time.
3. Asks for a table of contents of the unit first, then asks for one
   detailed, fully-bolded explanation **per section**, then a closing
   glossary - all inside one continuous chat session per unit. This is the
   fix for the "one summary is too short / misses material" problem: each
   section gets its own dedicated response instead of being squeezed into a
   single reply.
4. Merges every unit into one Markdown document (cover page + table of
   contents, then one section per unit, each starting on a new page) and
   renders it to a styled, right-to-left **Hebrew** PDF with a handwritten
   look (Playpen Sans Hebrew, notebook-ruled background). Gemini is
   instructed to write in Hebrew but keep technical terms/model names/
   acronyms in their original English form inline, matching normal academic
   convention (e.g. "אלגוריתם ה-BERT").

Results are cached per-unit under `output/<course>/cache/`, keyed by the
unit's source filenames + modified-times, so re-running after editing one
config or adding one new lecture doesn't re-call the API for units that
didn't change. Use `--no-cache` to force a full re-run.

## Setup

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The Gemini API key is already in `.env` (copied from `API_KEY.txt`, same
key used by the `pii_detection` project). If you rotate it, update `.env`.

## Usage

### Option A: point at a course folder (no config file needed)

If a course folder has a `Lectures` subfolder and/or an `Exercises` subfolder
(matching is case-insensitive and also accepts "Exrecises", "Tutorials"),
just run:

```
python main.py --course-dir "../NLP"
```

Every PDF found becomes its own unit, named after the file, in natural
filename order (`L2` sorts before `L10`). Course name and output folder are
taken from the directory name. To exclude a PDF (e.g. a duplicate/merged
file you don't want double-processed), rename it with a leading underscore,
e.g. `_exrecises.pdf` - the scanner skips those.

### Option B: hand-written YAML config

Use this if you want multiple lectures grouped into one unit, custom unit
names, or a non-standard folder layout. Copy `courses/data_science.yaml` as
a template, point `lectures_dir` / `exercises_dir` at the course's real
folders, and list which lecture files belong to which `unit`.

Helper to list PDFs in a folder (paste the output into a unit's
`lecture_files:`):
```
python main.py --list-pdfs "../NLP/Lectures"
```

Then run:
```
python main.py --course courses/data_science.yaml
```

Output PDF lands in `output/<course_name>/<course_name>_exam_prep.pdf`
either way.

Flags:
- `--no-cache` - re-call Gemini for every unit even if cached.
- `--refresh-context` - rebuild the full-course text digest (only needed if
  you added/edited a source PDF and want the cross-reference context to
  reflect it immediately; it also auto-rebuilds when file mtimes change).

## Cost

`gemini-3-flash-preview` is ~$0.50 / 1M input tokens and ~$3.00 / 1M output
tokens. The dominant cost driver is NOT the PDF pages (258 tokens/page) -
it's the full-course text digest (50-60K tokens for a typical course),
which gets resent as conversation history on *every* API call within a
unit's multi-turn chat, not just once. Based on real measured output from
four tested NLP lectures, a full ~700-page course with ~150 API calls
costs roughly **$5-6**; a smaller ~220-page course costs roughly **$2**.
Rerunning with the cache warm (no source changes) costs nothing.

To cut cost, raise `SECTION_BATCH_SIZE` in `src/pipeline.py` (currently 3
topics/call) - fewer, larger calls means fewer times the digest gets
resent, which is where most of the cost comes from. A single lecture with
~20 topics can mean ~9 sequential API calls and take 10-15 minutes
wall-clock - that's expected, not a hang.

Cached results are keyed by source files *and* a prompt version string, so
changing the language/formatting rules in `src/prompts.py` (bump
`PROMPT_VERSION`) automatically invalidates old cached results instead of
silently reusing stale output.

## Fonts

`assets/fonts/PlaypenSansHebrew.ttf` (OFL-licensed, from Google Fonts) is
embedded directly - it's the only font used, for both Hebrew and any inline
English/Latin terms, so mixed-language lines render consistently.

## Adding a new course

Just run `python main.py --course-dir "path/to/the/course/folder"` (see
Option A above) - no config file needed as long as the folder has a
Lectures/Exercises-style subfolder layout. Fall back to a YAML config
(Option B) only if you need custom unit grouping or a non-standard layout.
