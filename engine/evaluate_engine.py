# evaluate_engine.py
# Background worker: evaluate RAG system quality against a dataset.
# Supports quick (auto, demo-30) and full (llm, all) evaluation modes.

import os
import re
import sys
import json
import threading
import time
import pandas as pd
from datetime import datetime

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = os.path.join(BASE_DIR, "Dataset")
os.makedirs(DATASET_DIR, exist_ok=True)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# There's no UI to browse historical eval runs, only the most recent one
# matters — keep disk clutter down by pruning older eval_results_*.xlsx files
# every time a new one is written.
KEEP_LATEST_EVAL_FILES = 2
LATEST_RESULT_PATH     = os.path.join(BASE_DIR, "eval_results_latest.json")

# ── Job registry ─────────────────────────────────────────────────────────────
_jobs: dict = {}
_lock = threading.Lock()


def get_eval_job(job_id: str) -> dict:
    with _lock:
        return _jobs.get(job_id, {})


def _set(job_id: str, **kwargs):
    with _lock:
        if job_id not in _jobs:
            _jobs[job_id] = {}
        _jobs[job_id].update(kwargs)


# ── Dataset discovery ──────────────────────────────────────────────────────────
# Template files that live in Dataset/ purely so /download_dataset_example can
# serve them — they demonstrate the schema, not real evaluation data, so they
# must never appear as a selectable evaluation dataset.
_TEMPLATE_FILENAMES = {"example_sheet.xlsx"}

# ── Quick vs Full evaluation — which sheets each one reads ──────────────────
# Quick Evaluation (mode=auto, split="demo") combines every sheet prefixed
# "Demo_" — a fast, small keyword-scored subset.
# Full Evaluation (mode=llm, split="all") combines every sheet prefixed
# "Dataset_" — this is genuinely the FULL question set the file ships (all
# Dataset_* sheets concatenated and deduped by "id", see _combine_sheets
# below), scored by the Groq LLM rubric instead of keyword matching. It is
# not a separate curated subset — "Dataset_*" IS the complete dataset.
# This is why example_sheet.xlsx's template sheets are named Demo_Quick_example
# (→ Quick) and Dataset_example (→ Full): the prefix is what run_evaluation()
# and list_available_datasets() actually key off of, so renaming a sheet to
# anything not starting with "Demo_"/"Dataset_" makes it invisible to both
# evaluation modes and to the dataset dropdown.


def list_available_datasets() -> list:
    """
    Scans DATASET_DIR (Dataset/) for .xlsx files usable as evaluation datasets —
    any file containing at least one Dataset_* or Demo_* sheet. Picks up future
    files automatically, no hardcoded filenames (except _TEMPLATE_FILENAMES).
    Returns [{filename, demo_sheets, dataset_sheets}], sorted by filename.
    """
    results = []
    for fname in sorted(os.listdir(DATASET_DIR)):
        if not fname.lower().endswith(".xlsx") or fname.startswith("~$"):
            continue
        if fname in _TEMPLATE_FILENAMES:
            continue
        try:
            sheets = pd.ExcelFile(os.path.join(DATASET_DIR, fname)).sheet_names
        except Exception:
            continue
        demo_sheets    = [s for s in sheets if s.startswith("Demo_")]
        dataset_sheets = [s for s in sheets if s.startswith("Dataset_")]
        if demo_sheets or dataset_sheets:
            results.append({
                "filename":       fname,
                "demo_sheets":    demo_sheets,
                "dataset_sheets": dataset_sheets,
            })
    return results


def _combine_sheets(xl: "pd.ExcelFile", prefix: str):
    """
    Concats every sheet whose name starts with `prefix` (in file order),
    dedup'ing by the 'id' column (keep last — later sheets are the updated ones).
    Returns (DataFrame, matched_sheet_names). Empty DataFrame if no match.
    """
    matched = [s for s in xl.sheet_names if s.startswith(prefix)]
    if not matched:
        return pd.DataFrame(), matched

    combined = pd.concat([xl.parse(s) for s in matched], ignore_index=True)
    if "id" in combined.columns:
        combined = combined.drop_duplicates(subset="id", keep="last").reset_index(drop=True)
    return combined, matched


# ── Rubric ────────────────────────────────────────────────────────────────────
RUBRIC = {
    "legal_accuracy":      0.40,
    "citation_correct":    0.20,
    "retrieval_relevance": 0.20,
    "hallucination":       0.15,
    "clarity":             0.05,
}

RUBRIC_LABELS = {
    "legal_accuracy":      "Độ chính xác pháp lý",
    "citation_correct":    "Trích dẫn điều luật",
    "retrieval_relevance": "Mức độ liên quan ngữ cảnh",
    "hallucination":       "Kiểm soát bịa đặt",
    "clarity":             "Rõ ràng, dễ hiểu",
}


# ── Auto scorer (offline, fast) ───────────────────────────────────────────────
def _extract_article_numbers(article_ref: str) -> list:
    """Extract every 'Điều N[letter]' number named in a citation string.
    article_ref often carries extra numbers that must NOT be folded into the
    article number — a Khoản number ("Khoản 35 Điều 4"), a decree number/year
    ("Điều 17 Nghị định 168/2025/NĐ-CP"), or multiple articles joined by ";"
    ("Điều 27; Điều 38"). Matching only text that follows "Điều" keeps those
    separate instead of mashing every digit in the string into one number."""
    return re.findall(r'điều\s+(\d+[a-z]?)', article_ref, flags=re.IGNORECASE)


def _extract_law_numbers(article_ref: str) -> list:
    """Fallback for refs naming a law/decree with no 'Điều' component at all
    (e.g. "67/VBHN-VPQH 2025; Luật 76/2025/QH15") — match the doc number itself."""
    return re.findall(r'\d+/\d{4}/[A-ZĐ][A-ZĐ.\-]*', article_ref, flags=re.IGNORECASE)


_GRADING_INSTRUCTION_RE = re.compile(r'\s*Câu trả lời cần nêu[^.]*\.\s*$')


def _strip_grading_instructions(expected: str) -> str:
    """The Dataset_*/Demo_* expected_answer_vi column sometimes has a
    grading-instruction sentence appended directly onto the real answer text
    (e.g. "...nếu được chấp thuận. Câu trả lời cần nêu đúng căn cứ Điều 18 và
    không mở rộng sang lĩnh vực pháp luật khác nếu câu hỏi không yêu cầu.") —
    a note to whoever grades by hand, not part of the answer itself. Fed
    verbatim to the LLM judge as "Câu trả lời mẫu", that trailing sentence
    confuses it into marking the AI's answer as missing something (2026-07-27
    eval review: 32/103 knowledge_rule questions with >50% word-overlap to
    the real answer content still scored legal_accuracy<=1). Stripped once
    here so both scorers only ever see the actual legal content."""
    return _GRADING_INSTRUCTION_RE.sub('', expected).strip()


def _citation_grounded(generated: str, article_ref: str) -> bool:
    """Deterministic check: does `generated` actually name the article_ref's
    required "Điều N" (or, when article_ref has no Điều number, the law/decree
    number)? Same matching _auto_score()'s citation_correct axis uses —
    reused in _llm_score() as a floor on the judge's own citation_correct,
    because an 8B judge model doesn't reliably apply the "≥2 if the required
    article is present" instruction it's given on every question, even
    though the underlying answer is correct every time: 2026-07-27 eval
    review found 14/197 rows that DID cite the right article still scored
    citation_correct=0 from the judge alone."""
    gen_lower = generated.lower()
    art_nums = _extract_article_numbers(article_ref)
    if art_nums:
        return any(re.search(rf'điều\s+{re.escape(n)}\b', gen_lower) for n in art_nums)
    law_nums = _extract_law_numbers(article_ref)
    return any(n.lower() in gen_lower for n in law_nums) if law_nums else False


def _auto_score(question: str, generated: str, expected: str,
                article_ref: str, keywords: str, retrieved_context: str,
                expected_retrieved_context: str = "") -> dict:
    gen_lower = generated.lower()
    exp_lower = expected.lower()
    scores    = {}

    # expected_retrieved_context (Dataset_*/Demo_* column, previously unused
    # by the scorer) is a richer ground truth than the single article_ref —
    # a good answer to a "thành lập X" question legitimately cites several
    # articles together (hồ sơ + trình tự + định nghĩa, see
    # build_legal_basis_line() in rag_engine.py), and article_ref alone often
    # only names one of them. Any "Điều N" also named in
    # expected_retrieved_context counts as correct too, not just the one in
    # article_ref.
    ctx_nums     = set(_extract_article_numbers(expected_retrieved_context)) if expected_retrieved_context else set()
    art_nums     = _extract_article_numbers(article_ref)
    correct_nums = set(art_nums) | ctx_nums
    if correct_nums:
        cite_ok = any(re.search(rf'điều\s+{re.escape(n)}\b', gen_lower) for n in correct_nums)
    else:
        law_nums = _extract_law_numbers(article_ref)
        cite_ok = any(n.lower() in gen_lower for n in law_nums) if law_nums else False
    scores["citation_correct"] = 3 if cite_ok else 0

    kw_list    = [k.strip().lower() for k in keywords.split(';') if k.strip()]
    kw_hits    = sum(1 for kw in kw_list if kw in gen_lower)
    kw_ratio   = kw_hits / max(len(kw_list), 1)
    exp_words  = set(exp_lower.split())
    gen_words  = set(gen_lower.split())
    exp_overlap = len(exp_words & gen_words) / max(len(exp_words), 1)
    scores["legal_accuracy"] = min(3, round((kw_ratio * 2 + exp_overlap) * 1.5))

    if retrieved_context:
        ctx_lower = retrieved_context.lower()
        ctx_hits  = sum(1 for kw in kw_list if kw in ctx_lower)
        scores["retrieval_relevance"] = min(3, round((ctx_hits / max(len(kw_list), 1)) * 3))
    else:
        scores["retrieval_relevance"] = 0

    if correct_nums:
        # Dedupe before counting "wrong" hits — the answer body legitimately
        # repeats the same article numbers between "Căn cứ pháp lý" and the
        # citation footer's "Nguồn chính"/"Nguồn tham khảo" (see
        # build_legal_basis_line/build_citation in rag_engine.py), and a
        # richer answer citing several genuinely relevant articles (not just
        # the single one article_ref lists, but also anything named in
        # expected_retrieved_context — see correct_nums above) shouldn't be
        # double- or triple-penalized for each repeat of the same number —
        # only distinct unexpected numbers count as potential hallucination.
        cited_arts = set(re.findall(r'điều\s+(\d+[a-z]?)', gen_lower))
        # A number that actually appears in retrieved_context (what the RAG
        # pipeline really retrieved for this question) is grounded, not
        # invented — same principle rag_engine.py's own
        # validate_answer_citations() gate uses in production: only an
        # article never seen anywhere in context counts as a hallucination.
        # Without this, a "thành lập X" answer's legitimate multi-article
        # Căn cứ pháp lý (hồ sơ + trình tự + GCN alongside the one article
        # this dataset row names) got flagged as hallucinating on every
        # extra — genuinely correct — citation (2026-07-25/26 eval review).
        grounded_nums = correct_nums | (
            set(re.findall(r'điều\s+(\d+[a-z]?)', retrieved_context.lower())) if retrieved_context else set()
        )
        wrong_arts   = cited_arts - grounded_nums
        scores["hallucination"] = 3 if len(wrong_arts) == 0 else (2 if len(wrong_arts) <= 1 else 1)
    else:
        # No "Điều N" in the reference to check cited numbers against (law/decree-
        # name-only citation) — can't reliably detect a wrong article here.
        scores["hallucination"] = 3

    # Word count should measure the answer's own clarity, not the
    # system-appended citation footer (📖 Nguồn chính / 📎 Nguồn tham khảo —
    # see build_citation() in rag_engine.py) — that footer is structured
    # metadata, not prose, and its length has nothing to do with whether the
    # LLM's actual answer was clear. Counting it in made longer footers
    # (more secondary sources cited) push otherwise-good answers over the
    # 200-word ceiling and get penalized for it.
    body_only  = generated.split("📖 Nguồn chính:")[0]
    word_count = len(body_only.split())
    scores["clarity"] = 3 if 30 <= word_count <= 200 else (2 if word_count >= 15 else 1)

    scores["total"] = round(
        sum((scores[k] / 3.0) * RUBRIC[k] * 100 for k in RUBRIC), 1
    )
    return scores


def _extract_retry_after(e: Exception) -> float | None:
    """Pull the server-suggested wait time out of a rate-limit error instead
    of guessing. Groq's 429s carry a `retry-after` response header, and the
    error body also spells it out in prose ("Please try again in 4.348s") —
    either one is a more accurate wait than our fixed backoff schedule, which
    is just a blind guess at how long the rate-limit window is. Only falls
    back to the fixed schedule (in the caller) when neither is present."""
    response = getattr(e, "response", None)
    if response is not None:
        header = getattr(response, "headers", {}).get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
    match = re.search(r"try again in (\d+(?:\.\d+)?)s", str(e), re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


# ── LLM scorer (Groq) ─────────────────────────────────────────────────────────
def _llm_score(question: str, generated: str, expected: str,
               article_ref: str, groq_key: str) -> dict:
    """LLM-judge scoring. On total judge failure (Groq unreachable / response
    never parses to JSON after every retry) returns {"connection_error": True}
    instead of a score — this is an external connectivity/API failure, not a
    defect in the RAG answer, so callers must exclude the question from
    scoring entirely (own sheet, not counted in the average) rather than
    substitute any score for it. A hard 0 across all 5 rubric axes previously
    corrupted the average this way: a 2026-07-26 full-eval run zeroed 8/200
    questions, several of which had answers matching the expected answer
    almost verbatim — the RAG system wasn't at fault, the judge call was."""
    from langchain_groq import ChatGroq
    llm = ChatGroq(api_key=groq_key, model="llama-3.1-8b-instant", temperature=0)

    prompt = f"""Bạn là giáo viên chấm điểm câu trả lời pháp lý.

Câu hỏi: {question}
Câu trả lời của AI:
{generated}
Câu trả lời mẫu:
{expected}
Điều luật cần trích dẫn: {article_ref}

Hãy chấm điểm từ 0-3 cho mỗi tiêu chí sau và trả về JSON:
- legal_accuracy: Độ chính xác pháp lý (0=sai, 1=thiếu, 2=cơ bản đúng, 3=đúng đầy đủ)
- citation_correct: Trích dẫn điều luật (0=không có/sai, 1=mơ hồ, 2=đúng chính, 3=đúng đầy đủ).
  QUAN TRỌNG: nếu câu trả lời của AI có trích đúng "Điều luật cần trích dẫn" ở trên (dù ở phần Kết
  luận hay Căn cứ pháp lý), hãy chấm citation_correct TỐI THIỂU 2 điểm — kể cả khi câu trả lời còn
  liệt kê thêm các Điều luật liên quan khác trong phần Căn cứ pháp lý. CHỈ chấm dưới 2 khi Điều luật
  cần trích dẫn bị thiếu hoàn toàn hoặc bị trích sai số.
- retrieval_relevance: Nội dung dựa vào đúng điều luật (0-3)
- hallucination: Không bịa đặt (0=bịa nhiều, 1=có bịa, 2=ít bịa, 3=không bịa)
- clarity: Rõ ràng, dễ hiểu (0-3)

Chỉ trả về JSON, không giải thích. Ví dụ:
{{"legal_accuracy":2,"citation_correct":3,"retrieval_relevance":2,"hallucination":3,"clarity":3}}"""

    # Backoff: 15s, 30s, 60s, 90s, 120s
    wait_times = [15, 30, 60, 90, 120]
    for attempt in range(len(wait_times) + 1):
        try:
            response = llm.invoke(prompt).content.strip()
            # Anchor on "legal_accuracy" actually appearing inside the
            # matched span — a bare `\{[^}]+\}` can grab an unrelated
            # brace-delimited fragment from stray text around the model's
            # real answer. That fragment still parses as valid JSON, so no
            # exception fires, but sc.get(k, 0) then silently defaults every
            # rubric key to 0 — a false "0/0/0/0/0" that's indistinguishable
            # from a genuinely bad answer (2026-07-27 eval review: ELK027, an
            # answer that near-verbatim matched the expected text, scored
            # all-zero this way). Requiring every RUBRIC key be present
            # (checked below) rejects that fragment and retries instead.
            json_match = re.search(r'\{[^{}]*"legal_accuracy"[^{}]*\}', response)
            if json_match:
                sc = json.loads(json_match.group())
                if not all(k in sc for k in RUBRIC):
                    raise ValueError(f"judge JSON missing rubric keys: {sc}")
                # A real judge essentially never has grounds to score clarity
                # 0 on coherent, well-formed Vietnamese prose regardless of
                # whether the legal content is right or wrong — clarity is
                # about readability, not correctness. Every rubric axis
                # landing on exactly 0 at once (including clarity) is the
                # signature of the matched-fragment failure above slipping
                # through with a technically-valid-but-wrong JSON object
                # (ELU177/ELU184, 2026-07-27 review), not a real per-criterion
                # verdict — treat it as a bad response and retry.
                if all(sc.get(k, 0) == 0 for k in RUBRIC):
                    raise ValueError(f"judge returned all-zero scores, treating as invalid: {sc}")
                # Deterministic floor — don't trust the judge's
                # citation_correct blindly when the answer demonstrably cites
                # the right article (see _citation_grounded docstring).
                if _citation_grounded(generated, article_ref):
                    sc["citation_correct"] = max(sc.get("citation_correct", 0), 2)
                sc["total"] = round(
                    sum((sc.get(k, 0) / 3.0) * RUBRIC[k] * 100 for k in RUBRIC), 1
                )
                return sc
        except Exception as e:
            if attempt < len(wait_times):
                server_wait = _extract_retry_after(e)
                if server_wait is not None:
                    wait = server_wait
                    print(f"  [429] Rate limit - retry {attempt + 1}/{len(wait_times)} "
                          f"after {wait}s (server-reported)...")
                else:
                    wait = wait_times[attempt]
                    print(f"  [429] Rate limit - retry {attempt + 1}/{len(wait_times)} after {wait}s...")
                time.sleep(wait)
            else:
                print(f"  [FAIL] LLM scoring failed after all retries: {e}")

    # Groq unreachable, or its response never parsed to valid JSON, on every
    # retry — an external failure, not a scoreable answer.
    return {"connection_error": True}


def _prune_old_eval_files(keep: int = KEEP_LATEST_EVAL_FILES):
    """Keep only the `keep` most recently modified eval_results_*.xlsx runs.

    eval_low_score_*.xlsx / eval_connection_errors_*.xlsx are auxiliary sheets
    tied to one specific run — they share the same "<stem>_<split>_<mode>_<ts>"
    suffix as their eval_results_ file. They are NOT pruned by their own
    mtime ranking (that would let one survive independently of, or get
    deleted ahead of, the eval_results_ run it belongs to) — instead each one
    is deleted the moment its parent eval_results_ file ages out of the kept
    set, and never lingers as an orphan sheet with no matching run."""
    files = [
        f for f in os.listdir(BASE_DIR)
        if f.startswith("eval_results_") and f.lower().endswith(".xlsx")
    ]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(BASE_DIR, f)), reverse=True)

    kept_suffixes = {f[len("eval_results_"):] for f in files[:keep]}
    for f in files[keep:]:
        try:
            os.remove(os.path.join(BASE_DIR, f))
        except Exception:
            pass

    for prefix in ("eval_low_score_", "eval_connection_errors_"):
        for f in os.listdir(BASE_DIR):
            if not (f.startswith(prefix) and f.lower().endswith(".xlsx")):
                continue
            if f[len(prefix):] not in kept_suffixes:
                try:
                    os.remove(os.path.join(BASE_DIR, f))
                except Exception:
                    pass


# ── Low-score sheet export ────────────────────────────────────────────────────
# Score columns from RUBRIC, each on a 0-3 scale — a row is "lỗi" (needs a
# quick manual check) when ANY criterion scored 0 or 1 (< 2), per the
# threshold requested for the fast-review sheet.
_SCORE_COLS = [f"score_{k}" for k in RUBRIC]


def _low_score_mask(df_res: "pd.DataFrame"):
    return df_res[_SCORE_COLS].min(axis=1) < 2


def _export_low_score_sheet(df_res: "pd.DataFrame", out_dir: str, dataset_stem: str,
                            split: str, mode: str, ts: str) -> str | None:
    """Writes a filtered sheet of every question with at least one rubric
    criterion scored 0 or 1, so a low overall run can be triaged without
    re-reading all 200 rows. Returns the filename, or None if nothing qualifies."""
    low_df = df_res[_low_score_mask(df_res)]
    if low_df.empty:
        return None
    out_path = os.path.join(out_dir, f"eval_low_score_{dataset_stem}_{split}_{mode}_{ts}.xlsx")
    low_df.to_excel(out_path, index=False)
    return os.path.basename(out_path)


def _save_latest_result(summary_payload: dict):
    try:
        with open(LATEST_RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(summary_payload, f, ensure_ascii=False)
    except Exception:
        pass


def _summary_from_xlsx(path: str) -> dict:
    """Recompute a summary_payload-shaped dict from an existing eval_results
    xlsx's per-question score_* columns — used to backfill a score card for a
    run that finished before eval_results_latest.json existed."""
    df_res = pd.read_excel(path)
    if df_res.empty or "score_total" not in df_res.columns:
        return None

    avg_scores = {}
    for k in RUBRIC:
        col = f"score_{k}"
        if col in df_res.columns:
            avg_scores[k] = round(df_res[col].mean(), 2)

    by_type = {}
    by_diff = {}
    if "question_type" in df_res.columns:
        for qt, grp in df_res.groupby("question_type"):
            by_type[qt] = round(grp["score_total"].mean(), 1)
    if "difficulty" in df_res.columns:
        for diff, grp in df_res.groupby("difficulty"):
            by_diff[diff] = round(grp["score_total"].mean(), 1)

    return {
        "total_questions": len(df_res),
        "avg_total":       round(df_res["score_total"].mean(), 1),
        "avg_scores":      avg_scores,
        "rubric_labels":   RUBRIC_LABELS,
        "rubric_weights":  RUBRIC,
        "by_type":         by_type,
        "by_difficulty":   by_diff,
        "output_file":     os.path.basename(path),
        "mode":            "auto",
        "split":           "",
        "dataset_file":    "",
        "sheets_used":     [],
    }


def get_latest_eval_result() -> dict:
    """Returns the persisted summary of the most recent evaluation run for the
    Evaluate tab to show on open. Falls back to reconstructing it from the
    newest eval_results_*.xlsx on disk (and persists that reconstruction) if
    no run has completed since eval_results_latest.json was introduced.
    Returns None if no evaluation result exists at all."""
    if os.path.exists(LATEST_RESULT_PATH):
        try:
            with open(LATEST_RESULT_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    files = [
        f for f in os.listdir(BASE_DIR)
        if f.startswith("eval_results_") and f.lower().endswith(".xlsx")
    ]
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(os.path.join(BASE_DIR, f)), reverse=True)

    summary = _summary_from_xlsx(os.path.join(BASE_DIR, files[0]))
    if summary:
        _save_latest_result(summary)
    return summary


# ── Main background task ──────────────────────────────────────────────────────
def run_evaluation(job_id: str, mode: str, split: str, dataset_file: str = None):
    """
    Evaluate the RAG system.
    mode         : "auto" (fast, keyword matching) | "llm" (Groq-based, accurate)
    split        : "demo" (all Demo_* sheets in the file) | "all"/"test" (all Dataset_* sheets)
    dataset_file : filename (not path) of an .xlsx in DATASET_DIR (Dataset/), as
                   returned by list_available_datasets(). Falls back to the
                   newest known dataset file when omitted.
    """
    if dataset_file:
        # Sanitize: filename only, no path traversal outside DATASET_DIR.
        xlsx_path = os.path.join(DATASET_DIR, os.path.basename(dataset_file))
    else:
        # Default to the 200-updated file (latest dataset); fall back to 150 if absent
        xlsx_path = os.path.join(DATASET_DIR, "enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx")
        if not os.path.exists(xlsx_path):
            xlsx_path = os.path.join(DATASET_DIR, "enterprise_law_full_rag_chatbot_dataset_150.xlsx")

    _set(job_id, status="running", message="Đang tải dataset…", progress=0, scores=None)

    try:
        if not os.path.exists(xlsx_path):
            _set(job_id, status="failed",
                 message="❌ Không tìm thấy file dataset. Vui lòng import dataset trước.")
            return

        dataset_label = os.path.basename(xlsx_path)
        xl = pd.ExcelFile(xlsx_path)

        if split == "demo":
            df, matched = _combine_sheets(xl, "Demo_")
            if df.empty:
                _set(job_id, status="failed",
                     message=f"❌ File '{dataset_label}' không có sheet Demo — không thể chạy Quick Evaluation cho file này.")
                return
        else:
            df, matched = _combine_sheets(xl, "Dataset_")
            if df.empty:
                _set(job_id, status="failed",
                     message=f"❌ File '{dataset_label}' không có sheet Dataset — không thể chạy Full Evaluation cho file này.")
                return
            if split == "test" and "split" in df.columns:
                df = df[df["split"] == "test"]

        n = len(df)
        _set(job_id, message=f"Bắt đầu đánh giá {n} câu hỏi (mode={mode}, split={split})…", progress=0)

        # Resolve Groq key for LLM mode
        groq_key = None
        if mode == "llm":
            key_path = os.path.join(BASE_DIR, "groqkey.txt")
            if os.path.exists(key_path):
                with open(key_path) as f:
                    groq_key = f.read().strip()
            if not groq_key:
                mode = "auto"
                _set(job_id, message="⚠️ Không có Groq key — chuyển sang auto mode…")

        from engine.rag_engine import ask_rag

        results             = []
        connection_error_rows = []
        score_totals        = {k: [] for k in RUBRIC}
        totals               = []

        for idx, (_, row) in enumerate(df.iterrows()):
            q_id       = str(row.get('id', f'Q{idx}')).strip()
            question   = str(row.get('question_vi', '')).strip()
            expected   = _strip_grading_instructions(str(row.get('expected_answer_vi', '')).strip())
            art_ref    = str(row.get('article_reference', '')).strip()
            keywords   = str(row.get('retrieval_keywords', '')).strip()
            q_type     = str(row.get('question_type', '')).strip()
            difficulty = str(row.get('difficulty', '')).strip()
            exp_ctx    = str(row.get('expected_retrieved_context', '')).strip()
            if exp_ctx.lower() == 'nan':
                exp_ctx = ''

            if not question or question == 'nan':
                continue

            pct = round(((idx + 1) / n) * 100)
            _set(job_id,
                 message=f"[{idx + 1}/{n}] {question[:55]}…",
                 progress=pct)

            retrieved_context = ""
            try:
                result = ask_rag(question, return_debug=True)
                if isinstance(result, dict):
                    generated         = result["answer"]
                    retrieved_context = result.get("retrieved_context", "")
                else:
                    generated = str(result)
            except Exception as e:
                generated = f"ERROR: {e}"

            if mode == "llm" and groq_key:
                sc = _llm_score(question, generated, expected, art_ref, groq_key)
                time.sleep(3)   # ~20 req/min → stay under Groq free-tier limit
            else:
                sc = _auto_score(question, generated, expected, art_ref, keywords, retrieved_context, exp_ctx)

            if sc.get("connection_error"):
                # Groq unreachable for this question after every retry — an
                # external failure, not a defect in the RAG answer. Excluded
                # entirely from scoring/averages; goes in its own sheet instead.
                connection_error_rows.append({
                    "id":            q_id,
                    "question_type": q_type,
                    "difficulty":    difficulty,
                    "question":      question,
                    "generated":     generated,
                    "expected":      expected,
                    "article_ref":   art_ref,
                })
                continue

            for k in RUBRIC:
                score_totals[k].append(sc.get(k, 0))
            totals.append(sc["total"])

            results.append({
                "id":            q_id,
                "question_type": q_type,
                "difficulty":    difficulty,
                "question":      question,
                "generated":     generated,
                "expected":      expected,
                "article_ref":   art_ref,
                **{f"score_{k}": sc.get(k, 0) for k in RUBRIC},
                "score_total":   sc["total"],
            })

        avg_total  = round(sum(totals) / max(len(totals), 1), 1)
        avg_scores = {
            k: round(sum(score_totals[k]) / max(len(score_totals[k]), 1), 2)
            for k in RUBRIC
        }

        df_res  = pd.DataFrame(results)
        by_type = {}
        by_diff = {}
        if "question_type" in df_res.columns:
            for qt, grp in df_res.groupby("question_type"):
                by_type[qt] = round(grp["score_total"].mean(), 1)
        if "difficulty" in df_res.columns:
            for diff, grp in df_res.groupby("difficulty"):
                by_diff[diff] = round(grp["score_total"].mean(), 1)

        ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
        dataset_stem = os.path.splitext(dataset_label)[0]
        out_path     = os.path.join(BASE_DIR, f"eval_results_{dataset_stem}_{split}_{mode}_{ts}.xlsx")
        df_res.to_excel(out_path, index=False)

        low_score_file  = _export_low_score_sheet(df_res, BASE_DIR, dataset_stem, split, mode, ts)
        low_score_count = 0 if not low_score_file else int(_low_score_mask(df_res).sum())

        conn_err_file = None
        if connection_error_rows:
            conn_err_file = os.path.join(
                BASE_DIR, f"eval_connection_errors_{dataset_stem}_{split}_{mode}_{ts}.xlsx"
            )
            pd.DataFrame(connection_error_rows).to_excel(conn_err_file, index=False)
            conn_err_file = os.path.basename(conn_err_file)

        _prune_old_eval_files()

        summary_payload = {
            "total_questions":      len(results),
            "avg_total":            avg_total,
            "avg_scores":           avg_scores,
            "low_score_file":       low_score_file,
            "low_score_count":      low_score_count,
            "connection_error_file":  conn_err_file,
            "connection_error_count": len(connection_error_rows),
            "rubric_labels":   RUBRIC_LABELS,
            "rubric_weights":  RUBRIC,
            "by_type":         by_type,
            "by_difficulty":   by_diff,
            "output_file":     os.path.basename(out_path),
            "mode":            mode,
            "split":           split,
            "dataset_file":    dataset_label,
            "sheets_used":     matched,
        }
        _save_latest_result(summary_payload)

        done_msg = f"✅ Đánh giá hoàn tất! Điểm tổng: {avg_total}/100"
        if connection_error_rows:
            done_msg += f" (đã loại {len(connection_error_rows)} câu bị lỗi kết nối khỏi điểm tổng)"

        _set(job_id,
             status="done",
             message=done_msg,
             progress=100,
             scores=summary_payload)

    except Exception as e:
        import traceback
        traceback.print_exc()
        _set(job_id, status="failed", message=f"❌ Lỗi: {e}", scores=None)


# ── CLI entrypoint (with tqdm progress bar) ───────────────────────────────────
def _run_cli(mode: str, split: str):
    """Interactive terminal run with live progress bar and running score."""
    from tqdm import tqdm

    xlsx_path = os.path.join(DATASET_DIR, "enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx")
    if not os.path.exists(xlsx_path):
        xlsx_path = os.path.join(DATASET_DIR, "enterprise_law_full_rag_chatbot_dataset_150.xlsx")
    if not os.path.exists(xlsx_path):
        print("[ERROR] Dataset file not found. Run build_db_from_dataset.py first.")
        sys.exit(1)

    xl     = pd.ExcelFile(xlsx_path)
    sheets = xl.sheet_names

    demo_sheet = next((s for s in ["Demo_50", "Demo_30"] if s in sheets), None)
    if split == "demo" and demo_sheet:
        df = xl.parse(demo_sheet)
    else:
        ds_sheet = next(
            (s for s in ["Dataset_200", "Dataset_150"] if s in sheets),
            next((s for s in sheets if s.startswith("Dataset_")), None)
        )
        if ds_sheet is None:
            print("[ERROR] No Dataset_* sheet found in file.")
            sys.exit(1)
        df = xl.parse(ds_sheet)
        if split == "test" and "split" in df.columns:
            df = df[df["split"] == "test"]

    n = len(df)

    # Resolve Groq key
    groq_key = None
    if mode == "llm":
        key_path = os.path.join(BASE_DIR, "groqkey.txt")
        if os.path.exists(key_path):
            with open(key_path) as f:
                groq_key = f.read().strip()
        if not groq_key:
            print("[!] groqkey.txt not found — switching to auto mode")
            mode = "auto"

    print(f"\nEvaluating {n} questions  |  mode={mode}  |  split={split}")
    if mode == "llm":
        est = n * 5 // 60
        print(f"Estimated time: ~{est}-{est*2} minutes (LLM scoring + 3s throttle)\n")
    else:
        print()

    from engine.rag_engine import ask_rag

    results               = []
    connection_error_rows = []
    score_totals          = {k: [] for k in RUBRIC}
    totals                = []

    with tqdm(total=n, ncols=90, unit="q") as pbar:

        for idx, (_, row) in enumerate(df.iterrows()):
            q_id       = str(row.get("id",            f"Q{idx}")).strip()
            question   = str(row.get("question_vi",   "")).strip()
            expected   = _strip_grading_instructions(str(row.get("expected_answer_vi", "")).strip())
            art_ref    = str(row.get("article_reference",  "")).strip()
            keywords   = str(row.get("retrieval_keywords", "")).strip()
            q_type     = str(row.get("question_type", "")).strip()
            difficulty = str(row.get("difficulty",    "")).strip()
            exp_ctx    = str(row.get("expected_retrieved_context", "")).strip()
            if exp_ctx.lower() == "nan":
                exp_ctx = ""

            if not question or question == "nan":
                pbar.update(1)
                continue

            # ── Step 1: RAG answer ──
            pbar.set_description(f"[{q_id}] RAG...")
            retrieved_context = ""
            try:
                result = ask_rag(question, return_debug=True)
                if isinstance(result, dict):
                    generated         = result["answer"]
                    retrieved_context = result.get("retrieved_context", "")
                else:
                    generated = str(result)
            except Exception as e:
                generated = f"ERROR: {e}"

            # ── Step 2: Score ──
            pbar.set_description(f"[{q_id}] Scoring...")
            if mode == "llm" and groq_key:
                sc = _llm_score(question, generated, expected, art_ref, groq_key)
                time.sleep(3)
            else:
                sc = _auto_score(question, generated, expected, art_ref, keywords, retrieved_context, exp_ctx)

            if sc.get("connection_error"):
                connection_error_rows.append({
                    "id":            q_id,
                    "question_type": q_type,
                    "difficulty":    difficulty,
                    "question":      question,
                    "generated":     generated,
                    "expected":      expected,
                    "article_ref":   art_ref,
                })
                pbar.set_description(f"[{q_id}] connection error - skipped")
                pbar.update(1)
                continue

            for k in RUBRIC:
                score_totals[k].append(sc.get(k, 0))
            totals.append(sc["total"])

            results.append({
                "id":            q_id,
                "question_type": q_type,
                "difficulty":    difficulty,
                "question":      question,
                "generated":     generated,
                "expected":      expected,
                "article_ref":   art_ref,
                **{f"score_{k}": sc.get(k, 0) for k in RUBRIC},
                "score_total":   sc["total"],
            })

            running_avg = sum(totals) / max(len(totals), 1)
            pbar.set_description(f"[{q_id}]")
            pbar.set_postfix(score=f"{running_avg:.1f}/100", last=f"{sc['total']:.0f}")
            pbar.update(1)

    # ── Summary ──
    avg_total  = round(sum(totals) / max(len(totals), 1), 1)
    avg_scores = {k: round(sum(score_totals[k]) / max(len(score_totals[k]), 1), 2) for k in RUBRIC}

    df_res  = pd.DataFrame(results)
    by_type = {}
    by_diff = {}
    if "question_type" in df_res.columns:
        for qt, grp in df_res.groupby("question_type"):
            by_type[qt] = round(grp["score_total"].mean(), 1)
    if "difficulty" in df_res.columns:
        for d, grp in df_res.groupby("difficulty"):
            by_diff[d] = round(grp["score_total"].mean(), 1)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(BASE_DIR, f"eval_results_{split}_{mode}_{ts}.xlsx")
    df_res.to_excel(out_path, index=False)

    if connection_error_rows:
        conn_err_path = os.path.join(BASE_DIR, f"eval_connection_errors_{split}_{mode}_{ts}.xlsx")
        pd.DataFrame(connection_error_rows).to_excel(conn_err_path, index=False)

    W = 55
    print(f"\n{'='*W}")
    print(f"  TONG DIEM : {avg_total}/100   ({len(results)} cau, {mode} mode)")
    print(f"{'='*W}")
    for k, avg in avg_scores.items():
        label  = RUBRIC_LABELS.get(k, k)
        weight = RUBRIC.get(k, 0) * 100
        bar_w  = int((avg / 3.0) * 20)
        bar    = "[" + "#" * bar_w + "." * (20 - bar_w) + "]"
        print(f"  {label:<35} {bar} {avg:.2f}/3  ({weight:.0f}%)")

    if by_type:
        print(f"\n  Theo loai cau hoi:")
        for qt, v in sorted(by_type.items()):
            fill = int(v / 5)
            print(f"    {qt:<22} {'|'*fill} {v}/100")

    if by_diff:
        print(f"\n  Theo do kho:")
        for d, v in sorted(by_diff.items()):
            fill = int(v / 5)
            print(f"    {d:<10} {'|'*fill} {v}/100")

    print(f"\n  Ket qua : {os.path.basename(out_path)}")
    if connection_error_rows:
        print(f"  Loi ket noi (loai khoi diem): {len(connection_error_rows)} cau -> {os.path.basename(conn_err_path)}")
    print(f"{'='*W}\n")


if __name__ == "__main__":
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Evaluate RAG system (standalone)")
    parser.add_argument("--mode",  choices=["auto", "llm"], default="auto",
                        help="auto = fast keyword scoring | llm = Groq LLM scoring")
    parser.add_argument("--split", choices=["demo", "all", "test"], default="demo",
                        help="demo = 30 questions | all = full dataset | test = test split")
    args = parser.parse_args()

    _run_cli(mode=args.mode, split=args.split)
