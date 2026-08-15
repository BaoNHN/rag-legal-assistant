# smoke_test_model_migration.py
# One-off A/B smoke test for the 2026-08-16 Groq decommission of
# llama-3.1-8b-instant (chat) and llama-3.3-70b-versatile (judge), being
# replaced in production by openai/gpt-oss-20b (chat) and openai/gpt-oss-120b
# (judge) — see JUDGE_MODEL in engine/evaluate_engine.py and LLM_MODEL in
# engine/rag_engine.py for the live config this compares against.
#
# Runs both pipelines (old chat+judge vs new chat+judge) on the SAME
# questions so scores are directly comparable, and guarantees an exact N-vs-N
# comparison: any question where either pipeline hits a real connection
# error (RAG call throws, "Lỗi hệ thống", or the judge returns
# connection_error) is dropped and replaced with the next candidate from the
# pool instead of silently shrinking the sample (the prior manual run ended
# up 10-vs-9 this way).
import os
import sys
import json
import random
from datetime import datetime

import pandas as pd

# See app.py's matching reconfigure: judge output is Vietnamese, and a
# redirected/piped (non-console) stdout falls back to the system ANSI
# codepage, which UnicodeEncodeErrors on the diacritics.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from langchain_groq import ChatGroq
import engine.rag_engine as rag_engine
import engine.evaluate_engine as ev
from engine.groq_keys import current_key, reasoning_model_kwargs

OLD_CHAT_MODEL = "llama-3.1-8b-instant"
OLD_JUDGE_MODEL = "llama-3.3-70b-versatile"
NEW_CHAT_MODEL = "openai/gpt-oss-20b"
NEW_JUDGE_MODEL = "openai/gpt-oss-120b"

DATASET_PATH = os.path.join(BASE_DIR, "Dataset", "enterprise_law_full_rag_chatbot_dataset_200_updated.xlsx")
DATASET_SHEET = "Dataset_200"
RANDOM_SEED = 42


def set_chat_model(model: str):
    # Must patch LLM_MODEL too, not just llm: on a rate-limit key rotation,
    # _llm_invoke_with_retry rebuilds `llm` from the module-level LLM_MODEL
    # constant (engine/rag_engine.py:894), so leaving it stale would silently
    # snap a mid-test retry back to whichever model LLM_MODEL last pointed at.
    rag_engine.LLM_MODEL = model
    rag_engine.llm = ChatGroq(api_key=current_key(), model=model, temperature=0, **reasoning_model_kwargs(model))


def set_judge_model(model: str):
    ev.JUDGE_MODEL = model


def run_pipeline(question: str, chat_model: str):
    set_chat_model(chat_model)
    try:
        answer = rag_engine.ask_rag(question)
    except Exception as e:
        return None, f"exception: {e}"
    if isinstance(answer, str) and ev._RAG_ERROR_TEXT in answer:
        return None, "rag_system_error"
    return answer, None


def judge(question: str, answer: str, expected: str, article_ref: str, judge_model: str):
    set_judge_model(judge_model)
    sc = ev._llm_score(question, answer, expected, article_ref)
    if sc.get("connection_error"):
        return None
    return sc


def load_pool(difficulties=None):
    df = pd.read_excel(DATASET_PATH, sheet_name=DATASET_SHEET)
    if difficulties is not None:
        df = df[df["difficulty"].isin(difficulties)].copy()
    df = df.sample(frac=1, random_state=RANDOM_SEED).reset_index(drop=True)
    return df


def run_comparison(pool_df: pd.DataFrame, n: int, label: str):
    results = []
    skipped = []
    for _, row in pool_df.iterrows():
        if len(results) >= n:
            break
        qid = row["id"]
        question = row["question_vi"]
        expected = row["expected_answer_vi"]
        article_ref = row["article_reference"]
        difficulty = row["difficulty"]

        old_answer, old_err = run_pipeline(question, OLD_CHAT_MODEL)
        if old_answer is None:
            print(f"[{label}] SKIP {qid} ({difficulty}): old pipeline — {old_err}")
            skipped.append({"id": qid, "side": "old_rag", "reason": old_err})
            continue

        old_score = judge(question, old_answer, expected, article_ref, OLD_JUDGE_MODEL)
        if old_score is None:
            print(f"[{label}] SKIP {qid} ({difficulty}): old judge connection_error")
            skipped.append({"id": qid, "side": "old_judge", "reason": "connection_error"})
            continue

        new_answer, new_err = run_pipeline(question, NEW_CHAT_MODEL)
        if new_answer is None:
            print(f"[{label}] SKIP {qid} ({difficulty}): new pipeline — {new_err}")
            skipped.append({"id": qid, "side": "new_rag", "reason": new_err})
            continue

        new_score = judge(question, new_answer, expected, article_ref, NEW_JUDGE_MODEL)
        if new_score is None:
            print(f"[{label}] SKIP {qid} ({difficulty}): new judge connection_error")
            skipped.append({"id": qid, "side": "new_judge", "reason": "connection_error"})
            continue

        results.append({
            "id": qid,
            "difficulty": difficulty,
            "question": question,
            "old_answer": old_answer,
            "old_legal_accuracy": old_score["legal_accuracy"],
            "old_citation_correct": old_score["citation_correct"],
            "old_retrieval_relevance": old_score["retrieval_relevance"],
            "old_hallucination": old_score["hallucination"],
            "old_clarity": old_score["clarity"],
            "old_total": old_score["total"],
            "old_reason": old_score.get("reason", ""),
            "new_answer": new_answer,
            "new_legal_accuracy": new_score["legal_accuracy"],
            "new_citation_correct": new_score["citation_correct"],
            "new_retrieval_relevance": new_score["retrieval_relevance"],
            "new_hallucination": new_score["hallucination"],
            "new_clarity": new_score["clarity"],
            "new_total": new_score["total"],
            "new_reason": new_score.get("reason", ""),
            "delta_total": round(new_score["total"] - old_score["total"], 1),
        })
        print(f"[{label}] OK {qid} ({difficulty}) {len(results)}/{n} — old={old_score['total']} new={new_score['total']}")

    if len(results) < n:
        print(f"[{label}] WARNING: only {len(results)}/{n} valid pairs — pool exhausted")

    return pd.DataFrame(results), pd.DataFrame(skipped)


def save_report(results_df: pd.DataFrame, skipped_df: pd.DataFrame, label: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(BASE_DIR, f"smoke_migration_{label}_{ts}.xlsx")

    summary_rows = []
    for side in ("old", "new"):
        summary_rows.append({
            "side": side,
            "model_chat": OLD_CHAT_MODEL if side == "old" else NEW_CHAT_MODEL,
            "model_judge": OLD_JUDGE_MODEL if side == "old" else NEW_JUDGE_MODEL,
            "n": len(results_df),
            "avg_total": round(results_df[f"{side}_total"].mean(), 2) if len(results_df) else None,
            "avg_legal_accuracy": round(results_df[f"{side}_legal_accuracy"].mean(), 2) if len(results_df) else None,
            "avg_citation_correct": round(results_df[f"{side}_citation_correct"].mean(), 2) if len(results_df) else None,
            "avg_retrieval_relevance": round(results_df[f"{side}_retrieval_relevance"].mean(), 2) if len(results_df) else None,
            "avg_hallucination": round(results_df[f"{side}_hallucination"].mean(), 2) if len(results_df) else None,
            "avg_clarity": round(results_df[f"{side}_clarity"].mean(), 2) if len(results_df) else None,
        })
    summary_df = pd.DataFrame(summary_rows)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        results_df.to_excel(writer, sheet_name="Results", index=False)
        skipped_df.to_excel(writer, sheet_name="Skipped", index=False)

    print(f"[{label}] saved -> {out_path}")
    print(summary_df.to_string(index=False))
    return out_path


def main():
    # Smoke test 1: 10 questions, difficulty Medium or Hard mixed.
    pool1 = load_pool(["Medium", "Hard"])
    results1, skipped1 = run_comparison(pool1, 10, "test1_middle_hard")
    save_report(results1, skipped1, "test1_middle_hard")

    # Smoke test 2: 10 questions, difficulty Hard only.
    pool2 = load_pool(["Hard"])
    results2, skipped2 = run_comparison(pool2, 10, "test2_hard")
    save_report(results2, skipped2, "test2_hard")

    # Smoke test 3: 10 questions, difficulty unfiltered (random mix of
    # Easy/Medium/Hard straight off the full Dataset_200 pool).
    pool3 = load_pool(None)
    results3, skipped3 = run_comparison(pool3, 10, "test3_random")
    save_report(results3, skipped3, "test3_random")


if __name__ == "__main__":
    main()
