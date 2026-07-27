import os
import re
import unicodedata
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_groq import ChatGroq
from langchain.schema import Document
from difflib import SequenceMatcher
from collections import Counter

# =========================
# CONFIG
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "chroma_db")

STOPWORDS = {
    "theo", "là", "gì", "về", "của", "và",
    "trong", "được", "cho", "có", "khi"
}

# Keywords indicating out-of-scope questions (not business law)
OUT_OF_SCOPE_KEYWORDS = [
    "ly hôn", "li hôn", "hôn nhân", "gia đình", "ly thân", "kết hôn",
    "hình sự", "tội phạm", "khởi tố", "bắt giữ", "truy tố", "tù giam",
    "đất đai", "nhà ở", "bất động sản", "quyền sử dụng đất",
    "bảo hiểm xã hội", "bảo hiểm y tế", "tai nạn lao động",
    "thuế thu nhập cá nhân", "thuế giá trị gia tăng", "thuế tiêu thụ",
    "hải quan", "xuất nhập khẩu", "hành chính công",
]

# An OUT_OF_SCOPE_KEYWORDS hit is overridden when the question also carries
# one of these — it's very likely a genuine Enterprise Law question that
# only references the other domain as supporting context (a disqualifying
# condition, a funding source, a contributed asset), not a question actually
# about that domain. See _is_out_of_scope().
_BUSINESS_CONTEXT_SIGNALS = [
    "công ty", "doanh nghiệp", "hộ kinh doanh", "cổ đông", "cổ phần",
    "thành viên", "vốn điều lệ", "góp vốn", "thành lập", "đăng ký kinh doanh",
    "chủ sở hữu",
]

SIMILARITY_THRESHOLD = 1.3  # L2 distance; above this → not relevant enough
PROCEDURE_PATTERNS = [
    "trình tự",
    "thủ tục",
    "quy trình",
    "các bước",
    "hồ sơ",
    "nộp ở đâu",
    "thành lập",
]

CONDITION_PATTERNS = [
    "điều kiện",
    "yêu cầu",
    "cần có",
    "phải có",
]

DEFINITION_PATTERNS = [
    "là gì",
    "khái niệm",
    "định nghĩa",
    "quy định về",
]

with open(os.path.join(BASE_DIR, "groqkey.txt"), "r") as f:
    GROQ_API_KEY = f.read().strip()

# =========================
# EMBEDDING + VECTORSTORE
# =========================
embedding = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

vectorstore = Chroma(
    persist_directory=DB_PATH,
    embedding_function=embedding
)

retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

# =========================
# LLM
# =========================
llm = ChatGroq(
    api_key=GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0
)


# =========================
# CLEAN TEXT
# =========================
# Citation footer is appended programmatically by build_citation() after the
# LLM returns — these markers must never appear in the model's own text. If
# the model hallucinates its own fake "source" (seen in practice: invented
# document codes and lecture-material attributions), strip it here so it
# can't collide with or shadow the real citation.
_FAKE_CITATION_LINE = re.compile(
    r"^\s*[-•*]?\s*(📖|📎|Nguồn chính\s*:|Nguồn tham khảo\s*:|Tài liệu\s*:|Nguồn thu thập\s*:)",
    re.IGNORECASE
)


def clean_answer(text: str) -> str:
    text = re.sub(r"(?i)^xin chào.*?\n", "", text)
    lines = text.split("\n")
    seen, cleaned = set(), []
    for l in lines:
        if _FAKE_CITATION_LINE.match(l):
            continue
        if l.strip() and l not in seen:
            cleaned.append(l)
            seen.add(l)
    return "\n".join(cleaned).strip()


def validate_answer_citations(answer: str, context: str) -> bool:
    """Last-resort gate before an answer is shown to the user: every "Điều N"
    the model wrote in its own prose must actually appear in the retrieved
    context. build_citation() never derives the citation *footer* from the
    answer text at all (it's built strictly from best_doc/extra_docs
    metadata) — this catches a different failure: a wrong/invented article
    number left sitting in the answer BODY text, which build_citation()
    never touches, still reading as trustworthy to the user even with no
    citation attached to it."""
    answer_articles = set(re.findall(r'Điều\s+(\d+[a-z]?)', answer or "", flags=re.IGNORECASE))
    if not answer_articles:
        return True

    context_lower = (context or "").lower()
    return all(f"điều {n}" in context_lower for n in answer_articles)


# =========================
# CITATION SOURCE WHITELIST
# ─────────────────────────────
# Regex-stripping hallucinated citation lines (above) only catches text the
# model wrote in an obviously footer-shaped way. It can't tell a fabricated
# so_ky_hieu ("TH-LDN-20-2026") from a real one if it ever ends up in a
# Document's metadata (stale import, DB drift, manual edit, etc.). This
# whitelist is the harder guarantee: it's rebuilt from what's *actually*
# indexed in chroma_db right now, stored in chat.db (Const.CITATION_SOURCE,
# ";"-joined), and build_citation() refuses to print any so_ky_hieu that
# isn't in it.
# =========================
CITATION_SOURCE_KEY = "CITATION_SOURCE"


def refresh_citation_sources():
    """Rebuild the Const.CITATION_SOURCE whitelist from chroma_db's current
    so_ky_hieu values. Call this after any change to chroma_db (law import,
    dataset import) so the whitelist never lags behind what's really indexed.
    """
    from database.database import set_const

    try:
        data = vectorstore.get(include=["metadatas"])
    except Exception:
        return

    sources = set()
    for m in data["metadatas"]:
        val = (m.get("so_ky_hieu") or "").strip()
        # ";" is the whitelist's own delimiter — a source containing it would
        # corrupt parsing, so it's excluded rather than risk a false split/match.
        if val and ";" not in val:
            sources.add(val)

    set_const(CITATION_SOURCE_KEY, ";".join(sorted(sources)))


def is_known_citation_source(source: str) -> bool:
    """True if `source` is a so_ky_hieu that's actually indexed in chroma_db
    (per the last refresh_citation_sources() run)."""
    from database.database import get_const

    source = (source or "").strip()
    if not source:
        return False

    known_raw = get_const(CITATION_SOURCE_KEY)
    if not known_raw:
        # Whitelist never populated (e.g. very first run before any import
        # triggered a refresh) — fail open instead of blanking every citation.
        return True
    return source in known_raw.split(";")


def list_indexed_sources() -> list:
    """Distinct so_ky_hieu values currently indexed in chroma_db, with chunk
    counts — used by the admin UI to show what can be deleted."""
    try:
        data = vectorstore.get(include=["metadatas"])
    except Exception:
        return []

    metas = data["metadatas"]
    counts = Counter((m.get("so_ky_hieu") or "").strip() for m in metas)
    counts.pop("", None)
    importers = _first_importer_by_key(metas, "so_ky_hieu")
    return [
        {"so_ky_hieu": k, "chunk_count": v, "importer": importers.get(k, DEFAULT_IMPORTER)}
        for k, v in sorted(counts.items())
    ]


def delete_source(so_ky_hieu: str) -> int:
    """Delete every chunk in chroma_db whose so_ky_hieu matches, then rebuild
    the CITATION_SOURCE whitelist from what's left indexed — this is what
    keeps the whitelist from lagging a deleted import (see
    refresh_citation_sources above). Returns the number of chunks deleted.
    """
    so_ky_hieu = (so_ky_hieu or "").strip()
    if not so_ky_hieu:
        return 0

    existing = vectorstore.get(where={"so_ky_hieu": so_ky_hieu}, include=[])
    ids = existing.get("ids") or []
    if ids:
        vectorstore.delete(ids=ids)

    refresh_citation_sources()
    return len(ids)


# =========================
# MANAGE LAW — DATASET / SCENARIO SOURCES
# ─────────────────────────────
# Import Law tags every chunk with so_ky_hieu (see list_indexed_sources/
# delete_source above), so it needs no import_source tag. Dataset and Scenario
# imports carry their own "import_source" tag (set in import_dataset_engine /
# import_scenario_engine) so the Manage Law page can group and delete them by
# uploaded file without touching unrelated chunks that happen to share a
# so_ky_hieu (Dataset rows are all stamped so_ky_hieu="59/2020/QH14", the same
# code as the real imported law text).
# =========================
UNKNOWN_SOURCE_FILE_LABEL = "(không rõ tệp — nhập trước khi có tính năng theo dõi tệp)"

# Chunks imported before the "importer" field existed carry no such tag —
# backfill_importer_tags() below stamps them with this placeholder rather
# than leaving the admin UI's "Người nhập" column blank for old data.
DEFAULT_IMPORTER = "admin1"


def _first_importer_by_key(metas: list, key: str, default_group: str = "") -> dict:
    """First non-empty `importer` value seen per distinct `key` value —
    used to show one importer per grouped row in the admin source tables.
    `default_group` mirrors the same fallback label the row-grouping Counter
    uses (e.g. UNKNOWN_SOURCE_FILE_LABEL) so groups line up."""
    result = {}
    for m in metas:
        group = (m.get(key) or "").strip() or default_group
        if group and group not in result:
            result[group] = m.get("importer") or DEFAULT_IMPORTER
    return result


def backfill_importer_tags():
    """One-time migration for chunks indexed before the importer field
    existed. Idempotent — only touches chunks missing it, so it's cheap and
    safe to call on every startup."""
    try:
        data = vectorstore.get(include=["metadatas"])
    except Exception:
        return

    ids   = data.get("ids") or []
    metas = data.get("metadatas") or []

    update_ids, update_metas = [], []
    for doc_id, m in zip(ids, metas):
        if m.get("importer"):
            continue
        new_meta = dict(m)
        new_meta["importer"] = DEFAULT_IMPORTER
        update_ids.append(doc_id)
        update_metas.append(new_meta)

    if update_ids:
        vectorstore._collection.update(ids=update_ids, metadatas=update_metas)


def backfill_import_source_tags():
    """One-time migration for chunks indexed before import_source/source_file
    existed. Idempotent — only touches chunks that don't have import_source
    yet, so it's cheap and safe to call on every startup."""
    try:
        data = vectorstore.get(include=["metadatas"])
    except Exception:
        return

    ids   = data.get("ids") or []
    metas = data.get("metadatas") or []

    update_ids, update_metas = [], []
    for doc_id, m in zip(ids, metas):
        if m.get("import_source"):
            continue

        if "segment_index" in m:
            tag = "law"
        elif m.get("doc_type") == "scenario_qa":
            tag = "scenario"
        elif (m.get("so_ky_hieu") or "").strip() == "59/2020/QH14":
            tag = "dataset"
        else:
            # e.g. database/reference_source.py entries — not one of the 3
            # UI-driven import types, leave untagged.
            continue

        new_meta = dict(m)
        new_meta["import_source"] = tag
        if tag == "dataset" and not new_meta.get("source_file"):
            new_meta["source_file"] = UNKNOWN_SOURCE_FILE_LABEL
        update_ids.append(doc_id)
        update_metas.append(new_meta)

    if update_ids:
        vectorstore._collection.update(ids=update_ids, metadatas=update_metas)


def backfill_entity_type_tags():
    """One-time migration for chunks indexed before import pipelines started
    stamping entity_type at import time (see detect_entity_type() calls in
    import_law_engine.py / import_dataset_engine.py / import_scenario_engine.py).
    Computes it from the same text fields infer_doc_entity()'s runtime
    fallback already scans (page_content/topic/retrieval_keywords/title), so
    future retrieval hits the cheap explicit-field path instead of
    re-inferring from raw text on every question — matters once the corpus
    grows to tens of thousands of chunks. Idempotent — only touches chunks
    missing entity_type, safe to call on every startup."""
    try:
        data = vectorstore.get(include=["metadatas", "documents"])
    except Exception:
        return

    ids   = data.get("ids") or []
    metas = data.get("metadatas") or []
    docs  = data.get("documents") or []

    update_ids, update_metas = [], []
    for doc_id, m, content in zip(ids, metas, docs):
        if m.get("entity_type") or m.get("doc_type") in _ENTITY_AGNOSTIC_DOC_TYPES:
            continue
        text = " ".join([
            str(content or ""),
            str(m.get("topic") or ""),
            str(m.get("retrieval_keywords") or ""),
            str(m.get("title") or ""),
        ])
        entity_type = detect_entity_type(text)
        if not entity_type:
            continue
        new_meta = dict(m)
        new_meta["entity_type"] = entity_type
        update_ids.append(doc_id)
        update_metas.append(new_meta)

    if update_ids:
        vectorstore._collection.update(ids=update_ids, metadatas=update_metas)


def list_dataset_sources() -> list:
    """Distinct uploaded .xlsx filenames among dataset-origin chunks, with
    chunk counts."""
    try:
        data = vectorstore.get(where={"import_source": "dataset"}, include=["metadatas"])
    except Exception:
        return []

    metas = data["metadatas"]
    counts = Counter((m.get("source_file") or UNKNOWN_SOURCE_FILE_LABEL).strip() for m in metas)
    importers = _first_importer_by_key(metas, "source_file", UNKNOWN_SOURCE_FILE_LABEL)
    return [
        {"name": k, "chunk_count": v, "importer": importers.get(k, DEFAULT_IMPORTER)}
        for k, v in sorted(counts.items())
    ]


def delete_dataset_source(source_file: str) -> int:
    """Delete every dataset-origin chunk stamped with this source_file."""
    source_file = (source_file or "").strip()
    if not source_file:
        return 0

    existing = vectorstore.get(
        where={"$and": [{"import_source": "dataset"}, {"source_file": source_file}]},
        include=[],
    )
    ids = existing.get("ids") or []
    if ids:
        vectorstore.delete(ids=ids)

    refresh_citation_sources()
    return len(ids)


def list_scenario_sources() -> list:
    """Distinct uploaded .docx filenames among scenario_qa chunks, with chunk
    counts."""
    try:
        data = vectorstore.get(where={"doc_type": "scenario_qa"}, include=["metadatas"])
    except Exception:
        return []

    metas = data["metadatas"]
    counts = Counter((m.get("nguon_thu_thap") or UNKNOWN_SOURCE_FILE_LABEL).strip() for m in metas)
    importers = _first_importer_by_key(metas, "nguon_thu_thap", UNKNOWN_SOURCE_FILE_LABEL)
    return [
        {"name": k, "chunk_count": v, "importer": importers.get(k, DEFAULT_IMPORTER)}
        for k, v in sorted(counts.items())
    ]


def delete_scenario_source(name: str) -> int:
    """Delete every scenario_qa chunk whose source filename (nguon_thu_thap)
    matches."""
    name = (name or "").strip()
    if not name:
        return 0

    existing = vectorstore.get(
        where={"$and": [{"doc_type": "scenario_qa"}, {"nguon_thu_thap": name}]},
        include=[],
    )
    ids = existing.get("ids") or []
    if ids:
        vectorstore.delete(ids=ids)

    refresh_citation_sources()
    return len(ids)


# Populate the whitelist once at startup so it reflects chroma_db even if no
# import happens during this process's lifetime.
refresh_citation_sources()
backfill_import_source_tags()
backfill_importer_tags()
# backfill_entity_type_tags() runs at the true bottom of this module (after
# ask_rag) — it needs _ENTITY_AGNOSTIC_DOC_TYPES/detect_entity_type, both
# defined further down the file, not yet bound at this point in module load.


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def tokenize(text: str):
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return [w for w in text.split() if len(w) > 1]


# =========================
# ENTITY-TYPE DETECTION
# ─────────────────────────────
# The old flow let "TNHH một thành viên" questions get answered from "TNHH
# hai thành viên trở lên" documents (and vice versa) because they share most
# of their vocabulary — nothing hard-filtered on business entity type before
# reranking. This block detects the entity type implied by the question and
# by each candidate document, so filter_compatible_docs() below can drop
# documents belonging to a different entity type before they ever reach the
# context or the reranker.
#
# Real indexed metadata (see import_law_engine.py / import_dataset_engine.py)
# has no "entity_type" field — inference always falls back to scanning
# page_content/topic/retrieval_keywords/title. A doc matching BOTH the
# one-member and multi-member phrasing (a general provision that applies to
# either) intentionally returns None rather than guessing — None means "not
# entity-specific", so filter_compatible_docs() keeps it for any question.
# =========================
def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(text.lower().split())


_TONE_MARKS = "̣̀́̃̉"  # huyền, sắc, ngã, hỏi, nặng


def _strip_tone(text: str) -> str:
    """Drop Vietnamese tone marks (sắc/huyền/hỏi/ngã/nặng) while keeping the
    base vowel distinct (ă/â/ê/ô/ơ/ư/đ untouched) — makes the hardcoded
    intent/entity-type phrase lists below tolerant of a missing/wrong tone
    mark (e.g. "lâp" vs "lập"), a common typo that otherwise silently drops
    a question out of its intended bucket with no error: "thành lâp..." failed
    to match "thành lập" in _INTENT_PROCEDURE_PHRASES, intent fell back to
    "general", the procedure-intent retrieval boost in _score_doc() never
    fired, and an unrelated article (Điều 203, "chuyển đổi... thành công ty
    TNHH một thành viên") won on raw keyword overlap instead of Điều 21/74."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in _TONE_MARKS))


def _phrase_in(phrase: str, text: str) -> bool:
    """Tone-mark-typo-tolerant substring check, for the phrase lists below."""
    return _strip_tone(phrase) in _strip_tone(text)


_ENTITY_ONE_MEMBER_PHRASES = [
    "tnhh một thành viên", "tnhh 1 thành viên",
    "trách nhiệm hữu hạn một thành viên", "trách nhiệm hữu hạn 1 thành viên",
]
_ENTITY_MULTI_MEMBER_PHRASES = [
    "tnhh hai thành viên", "tnhh 2 thành viên", "hai thành viên trở lên",
    "trách nhiệm hữu hạn hai thành viên",
]
_ENTITY_PARTNERSHIP_PHRASES = ["công ty hợp danh", "thành viên hợp danh"]
_ENTITY_JSC_PHRASES = ["công ty cổ phần", "cổ đông", "cổ phần"]
_ENTITY_PRIVATE_PHRASES = ["doanh nghiệp tư nhân", "chủ doanh nghiệp tư nhân"]


def _detect_entity_type(text: str) -> str | None:
    t = normalize_text(text)
    has_one = any(_phrase_in(p, t) for p in _ENTITY_ONE_MEMBER_PHRASES)
    has_multi = any(_phrase_in(p, t) for p in _ENTITY_MULTI_MEMBER_PHRASES)
    if has_one and not has_multi:
        return "llc_one_member"
    if has_multi and not has_one:
        return "llc_multi_member"
    if any(_phrase_in(p, t) for p in _ENTITY_PARTNERSHIP_PHRASES):
        return "partnership"
    if any(_phrase_in(p, t) for p in _ENTITY_JSC_PHRASES):
        return "joint_stock_company"
    if any(_phrase_in(p, t) for p in _ENTITY_PRIVATE_PHRASES):
        return "private_enterprise"
    return None


# Public alias — import_law_engine.py / import_dataset_engine.py /
# import_scenario_engine.py call this at import time to stamp entity_type
# into chunk metadata upfront (see backfill_entity_type_tags() docstring
# above for why this matters at scale). Internal call sites in this module
# keep using the private name.
detect_entity_type = _detect_entity_type


_INTENT_PROCEDURE_PHRASES = [
    "thủ tục", "hồ sơ", "đăng ký thành lập", "cách thành lập", "trình tự",
    "nộp hồ sơ", "thành lập",
]
_INTENT_DEFINITION_PHRASES = ["là gì", "khái niệm", "định nghĩa"]
_INTENT_SCENARIO_PHRASES = ["anh a", "chị b", "ông c", "bà d", "tình huống", "có được không", "đúng hay sai"]


def detect_query_constraints(question: str) -> dict:
    q = normalize_text(question)
    intent = "general"
    if any(_phrase_in(p, q) for p in _INTENT_PROCEDURE_PHRASES):
        intent = "procedure"
    elif any(_phrase_in(p, q) for p in _INTENT_DEFINITION_PHRASES):
        intent = "definition"
    elif any(_phrase_in(p, q) for p in _INTENT_SCENARIO_PHRASES):
        intent = "scenario"
    return {"entity_type": _detect_entity_type(q), "intent": intent}


# Doc types that state a rule applicable to ALL business entity types (who
# may establish a business, civil capacity/age conditions, general
# registration procedure...) rather than one specific structure. Left to
# text inference, an incidental keyword — e.g. Điều 17 (quyền thành lập,
# entity-agnostic) mentions "mua cổ phần" only as one of several rights it
# lists, which _ENTITY_JSC_PHRASES matches on "cổ phần" alone — would wrongly
# scope a general provision to one entity type and get it excluded by
# filter_compatible_docs() for every OTHER question (e.g. Điều 17 never
# surfacing for a TNHH một thành viên question — 2026-07-25 eval review).
_ENTITY_AGNOSTIC_DOC_TYPES = {
    "establishment_eligibility", "civil_capacity_condition",
    "household_business_eligibility_condition",
}


def infer_doc_entity(doc) -> str | None:
    """Entity type of a retrieved Document. Checks an explicit entity_type/
    doc_type metadata field first (forward-compatible with future imports
    that tag it), then falls back to text inference — no currently indexed
    chunk actually carries that field yet."""
    metadata = doc.metadata or {}
    if metadata.get("doc_type") in _ENTITY_AGNOSTIC_DOC_TYPES:
        return None
    explicit = _detect_entity_type(metadata.get("entity_type") or metadata.get("doc_type") or "")
    if explicit:
        return explicit
    text = " ".join([
        str(doc.page_content or ""),
        str(metadata.get("topic") or ""),
        str(metadata.get("retrieval_keywords") or ""),
        str(metadata.get("title") or ""),
    ])
    return _detect_entity_type(text)


def filter_compatible_docs(question: str, docs: list) -> list:
    """Drop documents whose entity type conflicts with the one the question
    asks about. Docs with no detectable entity type (general provisions) are
    always kept — see module docstring above for why that's intentional."""
    expected_entity = detect_query_constraints(question)["entity_type"]
    if not expected_entity:
        return docs

    compatible = []
    for doc in docs:
        doc_entity = infer_doc_entity(doc)
        if doc_entity and doc_entity != expected_entity:
            continue
        compatible.append(doc)
    return compatible


# =========================
# QUERY REWRITE
# =========================
def _llm_invoke_with_retry(prompt: str, retries: int = 3) -> str:
    """Invoke LLM with retry on rate-limit/timeout errors."""
    import time
    for attempt in range(retries + 1):
        try:
            return llm.invoke(prompt).content.strip()
        except Exception as e:
            err = str(e).lower()
            if attempt < retries and any(k in err for k in ["rate", "timeout", "429", "503", "connection"]):
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                print(f"  ⚠ LLM retry {attempt + 1}/{retries} after {wait}s ({err[:40]})")
                time.sleep(wait)
                continue
            raise
    return ""


def rewrite_query(question: str) -> str:
    prompt = f"""Viết lại câu hỏi ngắn gọn, rõ nghĩa để tìm trong luật:

{question}"""
    try:
        return _llm_invoke_with_retry(prompt)
    except Exception:
        return question


# =========================
# EXTRACT TOPIC FROM QUESTION
# ─────────────────────────────
# Knowledge questions always follow: "quy định về [TOPIC] là gì?"
# This matches the KB_Articles topic field exactly (100% match rate).
# Used for ChromaDB metadata filtering to pinpoint the right article.
# =========================
def extract_topic_from_question(question: str) -> str | None:
    q = question.strip()
    # Pattern: "quy định về X là gì" or "X là gì" or "X theo quy định"
    patterns = [
        r'quy định về (.+?) là gì',
        r'quy định về (.+?) như thế nào',
        r'quy định về (.+?) gồm',
        r'về (.+?) là gì',
        r'(.+?) là gì theo Luật',
        r'^(.+?) là gì\??$',          # bare "X là gì?" with no prefix
        r'^(.+?) được hiểu là gì\??$',
        r'^(.+?) có nghĩa là gì\??$',
    ]
    for p in patterns:
        m = re.search(p, q, re.IGNORECASE)
        if m:
            topic = m.group(1).strip().lower()
            # Remove trailing noise
            topic = re.sub(r'\s*(theo luật.*|của luật.*)$', '', topic).strip()
            return topic
    return None


# =========================
# EXTRACT EXPLICIT ARTICLE NUMBER
# ─────────────────────────────
# Questions like "Điều 143" or "Điều 143 quy định gì?" name the article
# directly. Semantic embedding search is unreliable for this (bge-small
# is not tuned for Vietnamese legal numerals), so we match it against the
# article_number metadata exactly instead of relying on vector similarity.
# =========================
def extract_article_number_from_question(question: str) -> str | None:
    m = re.search(r'điều\s+(\d+[a-z]?)\b', question, re.IGNORECASE)
    return m.group(1) if m else None


# =========================
# KEYWORD-PHRASE RECALL
# ─────────────────────────────
# Token-overlap scoring against retrieval_keywords/page_content, independent
# of embedding similarity. Requires a minimum score so a single stray common
# word doesn't drag in an unrelated article — this is a recall booster for
# the semantic search, not a replacement for select_best_doc()'s reranking.
# =========================
def _keyword_recall(question: str, all_docs: list, top_n: int = 5, min_score: int = 4) -> list:
    q_words = {w for w in tokenize(question) if w not in STOPWORDS}
    if not q_words:
        return []

    scored = []
    for d in all_docs:
        kw_field = d.metadata.get("retrieval_keywords", "").lower()
        if not kw_field:
            continue
        content = d.page_content.lower()
        score = 0
        for w in q_words:
            if w in kw_field:
                score += 3
            elif w in content:
                score += 1
        if score >= min_score:
            scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_n]]


# =========================
# TOPIC-AWARE RETRIEVAL
# ─────────────────────────────
# FIX for knowledge questions scoring 57.7/100:
# 1. Try ChromaDB metadata filter on topic first (exact match)
# 2. Fall back to standard semantic search if no match
# This eliminates wrong article retrieval (Điều 34 instead of 36, etc.)
# =========================
def retrieve_docs(question: str, rewritten_q: str):
    topic = extract_topic_from_question(question)
    article_num = extract_article_number_from_question(question)

    try:
        results = vectorstore.get(include=["documents", "metadatas"])
        all_docs = []

        for doc_text, meta in zip(results["documents"], results["metadatas"]):
            all_docs.append(Document(
                page_content=doc_text,
                metadata=meta
            ))

        # ===== EXACT ARTICLE NUMBER MATCH =====
        # "Điều 143" etc. — filter on article_number metadata exactly instead
        # of trusting embedding similarity to find the right numbered article.
        if article_num:
            exact = [d for d in all_docs if d.metadata.get("article_number", "") == article_num]
            if exact:
                return exact[:5]

        # ===== STRICT TOPIC MATCH =====
        if topic:
            scored = []

            for d in all_docs:
                kb_topic = d.metadata.get("topic", "").strip().lower()

                if not kb_topic:
                    continue

                sim = similarity(topic, kb_topic)

                # bonus for exact containment
                if topic == kb_topic:
                    sim += 1.0
                elif topic in kb_topic:
                    sim += 0.3
                elif kb_topic in topic:
                    sim += 0.1

                scored.append((sim, d))

            scored.sort(key=lambda x: x[0], reverse=True)

            # confidence threshold
            if scored and scored[0][0] >= 0.55:
                return [d for _, d in scored[:5]]

        # ===== BARE-KEYWORD SUBSTRING MATCH =====
        # Short queries like "Tập đoàn" (no question phrasing, no topic/article
        # match above) — embedding similarity is unreliable for 1-3 word
        # Vietnamese legal terms with an English-tuned model. Scan titles and
        # content directly for the phrase instead.
        q_lower = question.lower().strip()
        if q_lower and len(q_lower.split()) <= 5:
            kw_hits = [
                d for d in all_docs
                if q_lower in d.page_content.lower() or q_lower in d.metadata.get("title", "").lower()
            ]
            if kw_hits:
                return kw_hits[:5]

        # ===== HYBRID: keyword-phrase recall + semantic search =====
        # For longer, naturally-phrased questions, semantic search alone can
        # still miss the right article even when its retrieval_keywords field
        # is a near-exact match for the question (originally observed with
        # bge-small-en-v1.5, an English-tuned model — kept as a safety net
        # after switching to the multilingual bge-m3, since keyword-token
        # overlap is a cheap independent signal regardless of embedding
        # quality) — e.g. "B 16 tuổi có được đăng ký hộ kinh doanh không?"
        # never surfaced the Điều 20/21 BLDS 2015 doc (keywords: "chưa đủ 18
        # tuổi", "hộ kinh doanh"...) in the semantic top-5. Score every doc by
        # keyword-token
        # overlap independent of embedding similarity, and merge those hits
        # ahead of the semantic results so select_best_doc() actually gets a
        # chance to consider them.
        kw_candidates = _keyword_recall(question, all_docs)

        results_with_scores = vectorstore.similarity_search_with_score(rewritten_q, k=5)
        # Filter: L2 distance lower = more similar; reject docs above threshold
        semantic_candidates = [doc for doc, score in results_with_scores if score <= SIMILARITY_THRESHOLD]

        merged, seen = [], set()
        for d in kw_candidates + semantic_candidates:
            if d.page_content not in seen:
                merged.append(d)
                seen.add(d.page_content)

        # ===== PROCEDURE-INTENT AUGMENTATION =====
        # "Thành lập công ty X như thế nào?" shares almost all of its
        # vocabulary ("công ty", "tnhh", "thành viên"...) with ownership/
        # definition articles, so those articles' keyword-overlap score
        # crowds out the actual hồ sơ/trình tự/cấp-GCN articles (doc_type
        # registration_llc / registration_procedure / erc_issuance etc.)
        # from ever reaching the top-5 kw/semantic candidates above. Pull
        # every establishment-shaped doc_type in directly so
        # select_best_doc()'s procedure-intent boost (see _score_doc) has
        # something to actually rank. filter_compatible_docs() downstream
        # still drops entity-type mismatches (e.g. registration_jsc docs
        # for a TNHH one-member question).
        if detect_query_constraints(question)["intent"] == "procedure":
            for d in all_docs:
                doc_type = d.metadata.get("doc_type", "")
                if doc_type in _ESTABLISHMENT_DOC_TYPES and d.page_content not in seen:
                    merged.append(d)
                    seen.add(d.page_content)

        return merged

    except Exception:
        return retriever.invoke(rewritten_q)


# =========================
# RERANK
# ─────────────────────────
# Scores against page_content + retrieval_keywords (weighted x2)
# + prefers KB_Articles docs over Q&A docs
# =========================
_OFFICIAL_SOURCE_MARKERS = [
    "cổng thông tin", "chính phủ", "công báo", "vbpl.vn", "chinhphu.vn", "văn bản hợp nhất",
]


def _score_doc(question: str, d, q_counter: Counter, intent: str) -> int:
    score = 0

    content = d.page_content.lower()
    metadata = d.metadata

    # keyword overlap
    for w, cnt in q_counter.items():
        if w in content:
            score += cnt

    # metadata keywords — single word match. Curated dataset entries
    # (import_source="dataset") hand-pick short, distinctive keyword phrases,
    # so a single-word hit there is a meaningful signal. Law-import chunks
    # (import_source="law") instead get retrieval_keywords auto-derived from
    # their own article heading (see import_law_engine.py) — a whole
    # sentence-topic, not curated, so common domain words it shares with
    # dozens of other articles in the same chapter ("công ty", "cổ đông"...)
    # would otherwise rack up the same 3x credit as a genuinely specific
    # curated match. Downweighting law-import matches to the same 1x as raw
    # content overlap fixed a regression where a topically-adjacent-but-wrong
    # official article (e.g. Điều 149 sharing "cổ đông" with the question)
    # outscored the actually-correct curated chunk (Điều 111) on a scenario
    # question — see 2026-07-25 eval-score-drop investigation.
    kw_field = metadata.get("retrieval_keywords", "")
    kw_text = kw_field.lower().replace(";", " ")
    kw_word_weight = 1 if metadata.get("import_source") == "law" else 3

    for w, cnt in q_counter.items():
        if w in kw_text:
            score += cnt * kw_word_weight

    # Multi-word keyword phrase match (high precision boost)
    # Matches "công ty hợp danh", "thành viên hợp danh" etc.
    q_lower = question.lower()
    for kw_phrase in kw_field.lower().split(";"):
        kw_phrase = kw_phrase.strip()
        if len(kw_phrase) > 8 and kw_phrase in q_lower:
            score += 10  # strong boost for exact phrase match

    # article reference bonus
    article_ref = metadata.get("article_reference", "")
    if article_ref:
        art_num = re.findall(r'\d+', article_ref)
        if art_num and art_num[0] in question:
            score += 5

    # prioritize KB articles
    nguon = metadata.get("nguon_thu_thap", "")
    if "KB_Articles" in nguon:
        score += 2

    # Source-tier priority: official law text > standardized KB > raw Q&A
    # dataset. Markers/thresholds taken from what's actually indexed
    # today (see engine.rag_engine module — law chunks are tagged
    # nguon_thu_thap="Cổng thông tin chính phủ"). Kept small (a tiebreaker,
    # not a dominant force) — it used to be +20, which combined with the
    # kw_word_weight above let a merely topically-adjacent official article
    # systematically outrank the actually-correct curated chunk on scenario
    # questions with repeated common terms (see kw_word_weight comment).
    nguon_lower = nguon.lower()
    if any(marker in nguon_lower for marker in _OFFICIAL_SOURCE_MARKERS):
        score += 6
    if "kb_articles_updated" in nguon_lower:
        score += 10
    if "dataset_200" in nguon_lower:
        score -= 3

    # Scenario/case-study docs are only appropriate for scenario-style
    # questions ("Anh A... có được không?") — for a general/procedure/
    # definition question, a scenario doc answering a *different*
    # specific fact pattern shouldn't outrank the general-purpose law text.
    is_scenario_doc = metadata.get("doc_type") == "scenario_qa" or \
        (metadata.get("question_type") or "").lower() == "scenario_case"
    if is_scenario_doc:
        score += 8 if intent == "scenario" else -15

    # "Thành lập công ty X như thế nào?" questions need the establishment
    # articles (hồ sơ, trình tự đăng ký, cấp GCN — see
    # _ESTABLISHMENT_DOC_TYPES below) — but those articles' retrieval_keywords
    # barely overlap with the generic "công ty/tnhh/thành viên" vocabulary
    # shared by dozens of ownership/definition articles. A small boost isn't
    # enough to close that gap, so this has to be large enough to reliably
    # land them in the top context slots.
    if intent == "procedure" and metadata.get("doc_type", "") in _ESTABLISHMENT_DOC_TYPES:
        score += 25

    # "Chuyển đổi loại hình doanh nghiệp" articles (convert_jsc_to_single_llc,
    # convert_llc_to_jsc...) share almost all of their vocabulary with a
    # plain "thành lập/là gì" question about the resulting entity type
    # ("công ty TNHH một thành viên" appears in both a definition article AND
    # "chuyển đổi công ty cổ phần THÀNH công ty TNHH một thành viên"), which
    # was enough to tie with — and by iteration-order luck, sometimes beat —
    # the actually relevant article (see 2026-07-25 "Điều 203 in Nguồn tham
    # khảo" report). Penalize unless the question is actually about
    # conversion.
    if metadata.get("doc_type", "").startswith("convert_") and not _phrase_in("chuyển đổi", question):
        score -= 20

    return score


def select_best_doc(question: str, docs):
    q_words = [w for w in tokenize(question) if w not in STOPWORDS]
    q_counter = Counter(q_words)
    intent = detect_query_constraints(question)["intent"]

    best_doc = None
    best_score = -1

    for d in docs:
        score = _score_doc(question, d, q_counter, intent)
        if score > best_score:
            best_score = score
            best_doc = d

    return best_doc


# =========================
# CLASSIFY QUESTION
# =========================
_DOC_TYPE_MAP = {
    "definition": "definition", "rights": "condition",
    "obligations": "condition", "prohibited": "condition",
    "establishment_eligibility": "condition",
    "registration_procedure": "procedure",
    "registration_private_enterprise": "procedure",
    "registration_partnership": "procedure",
    "registration_llc": "procedure",
    "registration_jsc": "procedure",
    "change_registration": "procedure",
    "notification_change": "procedure",
    "publication": "procedure",
    "asset_transfer": "procedure",
    "erc_issuance": "condition",
    "name_prohibitions": "condition",
    "asset_valuation": "condition",
    "legal_representative_duty": "condition",
    "dependent_units": "definition",
}

# Doc types specifically about STARTING a business — hồ sơ, trình tự đăng
# ký, cơ quan tiếp nhận, kết quả (GCN). Narrower than _DOC_TYPE_MAP's
# "procedure"/"condition" buckets above, which also catch doc types that
# have nothing to do with establishment (publication, name_prohibitions,
# asset_valuation, change_registration...) and would otherwise crowd a
# "thành lập X" question's context with irrelevant ongoing-compliance
# articles instead of the ones the question is actually asking for.
_ESTABLISHMENT_DOC_TYPES = {
    "registration_llc", "registration_partnership", "registration_jsc",
    "registration_private_enterprise", "registration_procedure",
    "erc_issuance", "erc_contents", "establishment_eligibility",
}


def classify_question(question: str, best_doc=None) -> str:
    q = question.lower()

    if any(_phrase_in(p, q) for p in PROCEDURE_PATTERNS):
        return "procedure"

    if any(_phrase_in(p, q) for p in CONDITION_PATTERNS):
        return "condition"

    if any(_phrase_in(p, q) for p in DEFINITION_PATTERNS):
        return "definition"

    return "general"


# =========================
# BUILD PROMPT
# =========================

# Two response tones (see build_prompt's `voice` param): "formal" (default)
# keeps the existing strict legal/scientific wording untouched; "casual"
# layers on plain-language instructions without relaxing any of the
# citation/hallucination QUY TẮC BẮT BUỘC below — only wording style changes.
_VOICE_INSTRUCTIONS = {
    "casual": (
        "\n🗣️ VĂN PHONG: Trả lời theo kiểu đời thường, dễ hiểu, như đang giải thích cho người "
        "không rành luật — dùng câu ngắn, từ ngữ thông dụng. Nếu bắt buộc phải dùng thuật ngữ "
        "pháp lý, giải thích ngắn gọn trong ngoặc đơn ngay sau đó. Vẫn phải giữ đúng cấu trúc "
        "Kết luận/Phân tích/Lưu ý và đúng số Điều luật như quy định — chỉ đổi cách diễn đạt, "
        "KHÔNG được bớt hay đổi nội dung pháp lý.\n"
    ),
}


def build_prompt(context: str, question: str, q_type: str,
                 article_ref: str = "", topic: str = "", voice: str = "formal") -> str:
    article_hint = ""
    if article_ref:
        line = article_ref
        if topic:
            line += f" — {topic}"
        article_hint = f"\nCăn cứ pháp lý: {line}\n"

    voice_hint = _VOICE_INSTRUCTIONS.get(voice, "")

    base = f"""Bạn là trợ lý pháp lý Việt Nam chuyên về Luật Doanh nghiệp.
{voice_hint}
⚠️ QUY TẮC BẮT BUỘC:
- Câu trả lời TỐI ĐA 200 từ.
- Không chào hỏi, không giải thích dư thừa.
- Không liệt kê toàn bộ văn bản luật — chỉ trả lời đúng nội dung câu hỏi.
- CĂN CỨ PHÁP LÝ phải lấy ĐÚNG từ tài liệu được cung cấp. KHÔNG tự suy diễn hay thay đổi số điều luật.
- Nếu tài liệu có metadata căn cứ pháp lý, phải sử dụng đúng điều luật đó.
- CHỈ được trích số Điều xuất hiện NGUYÊN VĂN trong phần "Tài liệu" bên dưới. Nếu không thấy số Điều liên quan trong tài liệu, hãy nói rõ là tài liệu không đề cập, KHÔNG được đoán hay lấy từ kiến thức chung.
- KHÔNG được tự đặt tên nguồn, mã văn bản, tên tác giả/giảng viên hay bất kỳ trích dẫn nào — phần nguồn tài liệu và phần "Căn cứ pháp lý" do hệ thống tự thêm vào sau, bạn không viết 2 phần đó.
- KHÔNG tự thêm tình tiết, số liệu hay điều kiện không có trong câu hỏi của người dùng.
- KHÔNG chuyển đổi loại hình doanh nghiệp nêu trong câu hỏi (vd: từ "một thành viên" sang "hai thành viên trở lên", hoặc ngược lại) khi trả lời.
- Nếu câu hỏi về công ty TNHH MỘT thành viên, KHÔNG được liệt kê "danh sách thành viên" trong hồ sơ đăng ký dù tài liệu có nhắc tới — mục này chỉ áp dụng cho công ty TNHH hai thành viên trở lên.
- KHÔNG dùng nội dung của một tình huống/case cụ thể để trả lời một câu hỏi mang tính khái quát, trừ khi người dùng thực sự hỏi về tình huống đó.
- Với bất kỳ nội dung nào trong tài liệu không áp dụng cho câu hỏi (sai loại hình doanh nghiệp, sai đối tượng...), hãy BỎ HẲN nội dung đó khỏi câu trả lời — KHÔNG liệt kê rồi ghi chú kiểu "(không áp dụng)", vì làm vậy khiến câu trả lời mất trọng tâm.
- Nếu Tài liệu bên dưới KHÔNG đủ để trả lời đúng câu hỏi, phải trả lời: "Không đủ dữ liệu để kết luận." thay vì suy diễn.
{article_hint}
Tài liệu:
{context}

Câu hỏi: {question}

"""
    STRUCT = (
        "\n\nTrả lời theo đúng cấu trúc sau (bắt buộc, mỗi mục một dòng riêng):\n"
        "**Kết luận:** [1 câu trả thẳng câu hỏi]\n"
        "**Phân tích:** [2-3 câu giải thích ngắn]\n"
        "**Lưu ý:** [1 điểm MỚI, KHÔNG lặp lại bất kỳ ý nào đã nêu ở Kết luận/Phân tích — "
        "ví dụ: so sánh với loại hình doanh nghiệp khác, trường hợp ngoại lệ, hoặc điểm dễ nhầm lẫn. "
        "Bỏ hẳn dòng này nếu không có điểm mới nào để thêm]\n"
        "Tổng cộng tối đa 200 từ. Không viết gì ngoài các mục trên."
    )
    if q_type == "procedure":
        base += (
            "Trong **Phân tích**, liệt kê các bước theo thứ tự (1, 2, 3...). "
            "Nếu câu hỏi về THÀNH LẬP doanh nghiệp và tài liệu có đề cập, "
            "cố gắng bao quát: điều kiện thành lập, hồ sơ cần nộp, trình tự/cơ quan tiếp nhận, "
            "và kết quả (Giấy chứng nhận đăng ký doanh nghiệp)."
        ) + STRUCT
    elif q_type == "condition":
        base += "Trong **Phân tích**, liệt kê ngắn gọn từng điều kiện." + STRUCT
    elif q_type == "definition":
        base += "Trong **Kết luận**, nêu định nghĩa. Trong **Phân tích**, giải thích chi tiết." + STRUCT
    else:
        base += STRUCT
    return base


# =========================
# BUILD CITATION
# =========================
# Canonical URL for the consolidated Enterprise Law text — used whenever a
# document is relabeled to "VBHN 67/VBHN-VPQH" below (see build_citation).
# Some indexed chunks carry so_ky_hieu="59/2020/QH14" with a source_url that
# points at the *original* law's lược đồ page instead of the VBHN 67 text,
# which — if printed as-is under the "VBHN 67" label — shows a URL that
# doesn't match the document name displayed to the user.
_VBHN_67_URL = "https://congbao.chinhphu.vn/van-ban/van-ban-hop-nhat-so-67-vbhn-vpqh-45865.htm"


def _detect_khoan(content: str, answer: str) -> str | None:
    """A law chunk is indexed as a whole Điều, but the answer usually only
    draws on one khoản inside it (nhận xét 25/7 flagged citing bare "Điều 74"
    when the answer only used khoản 1's definition sentence). Split the
    chunk into its numbered khoản and, only when the answer's wording
    overlaps one of them more than any other, name that khoản instead of
    the whole article — ambiguous cases fall back to citing the article as
    a whole rather than guessing."""
    khoan_matches = list(re.finditer(r'(?:^|\n)(\d+)\.\s+(.+?)(?=\n\d+\.\s|\Z)', content, re.DOTALL))
    if len(khoan_matches) < 2:
        return None
    answer_words = {w for w in tokenize(answer) if w not in STOPWORDS}
    if not answer_words:
        return None
    best_n, best_score, second_score = None, -1, -1
    for m in khoan_matches:
        words = {w for w in tokenize(m.group(2)) if w not in STOPWORDS}
        score = len(answer_words & words)
        if score > best_score:
            best_n, second_score, best_score = m.group(1), best_score, score
        elif score > second_score:
            second_score = score
    if best_n is not None and best_score > 0 and best_score > second_score:
        return best_n
    return None


def _resolve_primary_article_ref(meta: dict, content: str = "", answer: str = "") -> str:
    """Primary article reference, upgraded to "Khoản N Điều X" when the
    answer clearly drew from just one khoản inside the article (see
    _detect_khoan). Shared by build_citation() and build_legal_basis_line()
    so Nguồn chính and Căn cứ pháp lý can never disagree with each other —
    both are derived from best_doc's own metadata, never from LLM output.
    Mixing in article numbers the LLM happened to write let a number it
    recalled (or invented) get credited to a source document that never
    mentioned it, and in whatever order the answer happened to mention it
    (e.g. "Điều 76; Điều 74" when the actual primary basis was Điều 74)."""
    article_ref = meta.get("article_reference", "")
    if content and answer and article_ref and ";" not in article_ref \
            and not article_ref.lower().startswith("khoản"):
        khoan_n = _detect_khoan(content, answer)
        if khoan_n:
            article_ref = f"Khoản {khoan_n} {article_ref}"
    return article_ref


def build_legal_basis_line(meta: dict, secondary_docs=None, content: str = "", answer: str = "") -> str:
    """Builds the answer body's "Căn cứ pháp lý" line entirely from
    best_doc/extra_docs metadata — replaces an earlier version where the LLM
    was asked to write this line itself from a prompt hint, which in
    practice could drift from Nguồn chính (LLM wrote "Điều 74" while Nguồn
    chính correctly said "Điều 21" — nhận xét 25/7 follow-up). Lists the same
    articles as Nguồn chính + Nguồn tham khảo combined, since a "thành lập X"
    question usually genuinely needs several articles together (hồ sơ +
    trình tự + định nghĩa), not just one."""
    so_ky_hieu = meta.get("so_ky_hieu", "")
    if so_ky_hieu and not is_known_citation_source(so_ky_hieu):
        so_ky_hieu = ""

    primary_ref = _resolve_primary_article_ref(meta, content, answer)
    if not primary_ref:
        return ""

    refs = [primary_ref]
    seen = {meta.get("article_reference", "")}
    same_law_only = True

    for doc in (secondary_docs or [])[:3]:
        m = doc.metadata
        s_article = m.get("article_reference", "")
        s_ky_hieu = m.get("so_ky_hieu", "")
        if not s_article or s_article in seen:
            continue
        if s_ky_hieu and not is_known_citation_source(s_ky_hieu):
            continue
        seen.add(s_article)
        refs.append(s_article)
        if s_ky_hieu and s_ky_hieu != meta.get("so_ky_hieu", ""):
            same_law_only = False

    line = ", ".join(refs)
    if same_law_only:
        law_name = f"{meta.get('loai_van_ban', '')} {so_ky_hieu}".strip()
        if "67/VBHN" in so_ky_hieu or "59/2020" in so_ky_hieu:
            law_name = "Văn bản hợp nhất Luật Doanh nghiệp số 67/VBHN-VPQH năm 2025"
        if law_name:
            line += f" — {law_name}"
    return line


def build_citation(meta: dict, secondary_docs=None, content: str = "", answer: str = "") -> str:
    article_ref = _resolve_primary_article_ref(meta, content, answer)
    topic = meta.get("topic", "")
    so_ky_hieu = meta.get("so_ky_hieu", "")
    loai = meta.get("loai_van_ban", "")
    source_url = meta.get("source_url", "")

    # Refuse to print a document code that isn't actually indexed in chroma_db
    # right now — catches stale/fabricated so_ky_hieu before it reaches the user.
    if so_ky_hieu and not is_known_citation_source(so_ky_hieu):
        so_ky_hieu = ""
        source_url = ""

    parts = []
    if article_ref:
        line = article_ref
        if topic and ";" not in article_ref:
            line += f" ({topic})"
        parts.append(line)

    law_name = f"{loai} {so_ky_hieu}".strip()
    # Always cite the consolidated text (67/VBHN-VPQH 2025) for Enterprise Law
    if ("67/VBHN" in so_ky_hieu or "59/2020" in so_ky_hieu or
            "67/VBHN" in law_name or "59/2020" in law_name):
        law_name = "Văn bản hợp nhất Luật Doanh nghiệp số 67/VBHN-VPQH năm 2025"
        # Relabeled to the VBHN 67 text — the URL must point there too,
        # not at whichever page this specific chunk's metadata happened to
        # carry (see _VBHN_67_URL docstring above).
        source_url = _VBHN_67_URL
    if law_name:
        parts.append(law_name)

    citation = " — ".join(parts) if parts else meta.get("nguon_thu_thap", "")
    result = f"\n\n📖 Nguồn chính: {citation}"
    if source_url and ("vbpl.vn" in source_url or "congbao.chinhphu.vn" in source_url):
        result += f"\n🔗 {source_url}"

    # Secondary sources (max 3, deduplicated, short format)
    if secondary_docs:
        primary_ref = meta.get("article_reference", "")
        seen_refs = {primary_ref} if primary_ref else set()
        secondary_lines = []

        for doc in secondary_docs[:3]:
            m = doc.metadata
            s_article = m.get("article_reference", "")
            s_topic = m.get("topic", "")
            s_ky_hieu = m.get("so_ky_hieu", "")

            if not s_article or s_article in seen_refs:
                continue
            seen_refs.add(s_article)

            if s_ky_hieu and not is_known_citation_source(s_ky_hieu):
                s_ky_hieu = ""

            if "67/VBHN" in s_ky_hieu or "59/2020" in s_ky_hieu:
                s_law = "VBHN 67/VBHN-VPQH 2025"
            else:
                s_law = s_ky_hieu[:20] if s_ky_hieu else ""

            s_line = s_article
            if s_topic and ";" not in s_article:
                short_topic = s_topic[:25] + "…" if len(s_topic) > 25 else s_topic
                s_line += f" ({short_topic})"
            if s_law:
                s_line += f" — {s_law}"

            if len(s_line) > 100:
                s_line = s_line[:97] + "…"

            secondary_lines.append(f"• {s_line}")

        if secondary_lines:
            result += "\n📎 Nguồn tham khảo:\n" + "\n".join(secondary_lines)

    return result


# =========================
# MAIN
# =========================
def _is_out_of_scope(question: str) -> bool:
    q = question.lower()
    if not any(kw in q for kw in OUT_OF_SCOPE_KEYWORDS):
        return False
    # An OUT_OF_SCOPE_KEYWORDS hit alone over-blocks: "C đang bị truy cứu
    # trách nhiệm hình sự nhưng muốn thành lập doanh nghiệp tư nhân..." is a
    # genuine Enterprise Law eligibility question (Điều 17) that only
    # mentions "hình sự" as a disqualifying condition, not a criminal-law
    # question. Same for "gia đình góp vốn" (family funding a company) and
    # "góp vốn bằng quyền sử dụng đất" (land-use rights as capital) — both
    # got wrongly blocked in the 2026-07-25 eval review. If the question also
    # carries a clear Enterprise Law signal, treat it as in-scope; the answer
    # pipeline still falls back to "Không đủ dữ liệu để kết luận." if nothing
    # relevant is actually retrieved.
    return not any(sig in q for sig in _BUSINESS_CONTEXT_SIGNALS)


# ── Meta: hỏi về database/hệ thống ──────────────────
_META_DB_PATTERNS = [
    r'(database|cơ sở dữ liệu|hệ thống).*(bao nhiêu|lưu|chứa)',
    r'(đang lưu|lưu giữ|có chứa).*(bao nhiêu)',
    r'bao nhiêu (điều luật|văn bản|tài liệu).*(lưu|database|hệ thống)',
]

def _is_meta_db_question(question: str) -> bool:
    q = question.lower()
    return any(re.search(p, q) for p in _META_DB_PATTERNS)

def _answer_meta_db() -> str:
    try:
        data   = vectorstore.get(include=["metadatas"])
        metas  = data["metadatas"]
        total  = len(metas)
        refs   = {m.get("article_reference", "") for m in metas if m.get("article_reference")}
        refs.discard("")
        nguons = {m.get("so_ky_hieu", "") for m in metas if m.get("so_ky_hieu")}
        nguons.discard("")
        return (
            f"**Kết luận:** Hệ thống đang lưu {total} đoạn văn bản, "
            f"tương ứng {len(refs)} điều luật khác nhau.\n"
            f"**Phân tích:** Dữ liệu được lấy từ {len(nguons)} nguồn văn bản pháp luật: "
            f"{', '.join(sorted(nguons))}. "
            f"Mỗi điều luật có thể được lưu thành nhiều đoạn để tối ưu tìm kiếm.\n"
            f"**Lưu ý:** Con số này có thể tăng khi giáo viên import thêm văn bản pháp luật mới."
            f"\n\n📖 Nguồn: ChromaDB — RAG Legal Assistant"
        )
    except Exception as e:
        return f"❌ Không thể truy vấn database: {e}"


# ── Meta: hỏi tổng số điều của bộ luật ──────────────
_META_LAW_COUNT_PATTERNS = [
    r'luật doanh nghiệp.*(có|gồm|bao gồm).*(bao nhiêu điều|mấy điều)',
    r'bao nhiêu điều.*(luật doanh nghiệp)',
    r'(59/2020|67/vbhn|luật doanh nghiệp).*(bao nhiêu|mấy).*(điều|chương)',
    r'(bao nhiêu|mấy) (điều|chương).*(luật doanh nghiệp|59/2020|67/vbhn)',
    r'(cấu trúc|cơ cấu|gồm).*(chương|điều).*(luật doanh nghiệp)',
]

def _is_meta_law_count_question(question: str) -> bool:
    q = question.lower()
    return any(re.search(p, q) for p in _META_LAW_COUNT_PATTERNS)

def _answer_meta_law_count(question: str) -> str:
    prompt = (
        "Bạn là trợ lý pháp lý Việt Nam. Trả lời ngắn gọn câu hỏi sau về cấu trúc Luật Doanh nghiệp.\n"
        "Trả lời theo cấu trúc:\n"
        "**Kết luận:** [1 câu trực tiếp]\n"
        "**Phân tích:** [2-3 câu chi tiết về cấu trúc luật]\n"
        "**Lưu ý:** [điểm cần nhớ]\n\n"
        f"Câu hỏi: {question}"
    )
    try:
        answer = _llm_invoke_with_retry(prompt)
        return answer + "\n\n📖 Nguồn: Kiến thức tổng quát về Luật Doanh nghiệp Việt Nam"
    except Exception as e:
        return f"❌ Lỗi: {e}"


def ask_rag(question: str, return_debug: bool = False, voice: str = "formal"):
    try:
        question = str(question)

        # Pre-check: question outside business law scope
        if _is_out_of_scope(question):
            return "⚠️ Câu hỏi này nằm ngoài phạm vi dữ liệu Luật Doanh nghiệp của hệ thống. Vui lòng đặt câu hỏi liên quan đến Luật Doanh nghiệp."

        # Pre-check: meta about law structure (check before db — more specific)
        if _is_meta_law_count_question(question):
            return _answer_meta_law_count(question)

        # Pre-check: meta question about the database
        if _is_meta_db_question(question):
            return _answer_meta_db()

        # STEP 1: rewrite — SKIP if topic is extractable
        # Knowledge questions follow "quy định về X là gì?" pattern.
        # Topic filter works directly on original question → no LLM rewrite needed.
        # This saves one Groq API call per knowledge question, reducing rate limit hits.
        topic = extract_topic_from_question(question)
        better_q = question if topic else rewrite_query(question)

        # STEP 2: topic-aware retrieval (fixes knowledge question accuracy)
        docs = retrieve_docs(question, better_q)

        if not docs:
            return "⚠️ Không tìm thấy thông tin đủ liên quan trong cơ sở dữ liệu. Vui lòng hỏi rõ hơn hoặc kiểm tra câu hỏi có thuộc phạm vi Luật Doanh nghiệp không."

        # STEP 2.1: drop documents whose business-entity type conflicts with
        # the question (e.g. "TNHH một thành viên" question vs "TNHH hai
        # thành viên trở lên" document) — see filter_compatible_docs above.
        docs = filter_compatible_docs(question, docs)
        if not docs:
            return (
                "⚠️ Hệ thống chưa tìm thấy nguồn phù hợp đúng loại hình doanh nghiệp "
                "và đúng nội dung câu hỏi. Vui lòng cung cấp câu hỏi chi tiết hơn."
            )

        # STEP 3: rerank — use the ORIGINAL question, not the LLM-rewritten
        # one: rewrite_query() can drop the exact qualifier ("một thành viên"
        # vs "hai thành viên") that distinguishes otherwise near-identical docs.
        best_doc = select_best_doc(question, docs)
        if not best_doc:
            return "❌ Không đủ dữ liệu để trả lời câu hỏi này."

        # STEP 4: rank the full candidate-document pool (best_doc first) —
        # used both to fill "extra context" docs and, per STEP 5-8.1's retry
        # loop below, as fallback primary documents.
        # (Retrieval order puts kw/semantic hits first and any
        # procedure-intent augmented docs last — a positional docs[:3]
        # would silently drop exactly the hồ sơ/trình tự articles the
        # procedure-intent boost above was added to surface.)
        q_counter = Counter([w for w in tokenize(question) if w not in STOPWORDS])
        intent = detect_query_constraints(question)["intent"]
        ranked_extra = sorted(
            (d for d in docs if d.page_content != best_doc.page_content),
            key=lambda d: _score_doc(question, d, q_counter, intent),
            reverse=True,
        )
        if intent == "procedure":
            # Fill the extra context slots with establishment-doc-type docs
            # first (see _ESTABLISHMENT_DOC_TYPES) so a "thành lập X"
            # question's hồ sơ/trình tự/cấp GCN articles all make it into
            # context, even when a single one of them still scores below an
            # ownership/rights article that shares more raw vocabulary.
            procedure_first = [
                d for d in ranked_extra
                if d.metadata.get("doc_type", "") in _ESTABLISHMENT_DOC_TYPES
            ]
            others = [d for d in ranked_extra if d not in procedure_first]
            ranked_extra = procedure_first + others
        all_ranked = [best_doc] + ranked_extra

        # STEP 5-8.1: generate an answer from the best-ranked doc; if
        # validate_answer_citations() rejects it (the answer names an "Điều N"
        # never actually seen in that doc's context — usually the LLM
        # inventing/misremembering a number), retry with the 2nd- and then
        # 3rd-best-ranked doc as primary before giving up. Each attempt
        # rebuilds its own context/classification/prompt around its primary doc.
        winner = None
        for primary in all_ranked[:3]:
            extra_docs   = [d for d in all_ranked if d is not primary][:3]
            context_parts = [primary.page_content] + [d.page_content for d in extra_docs]
            context = "\n\n---\n\n".join(context_parts)[:3000]

            q_type      = classify_question(question, primary)
            article_ref = primary.metadata.get("article_reference", "")
            doc_topic   = primary.metadata.get("topic", "")
            prompt      = build_prompt(context, question, q_type, article_ref, doc_topic, voice=voice)

            answer = _llm_invoke_with_retry(prompt)
            answer = clean_answer(answer)

            if validate_answer_citations(answer, context):
                winner = (primary, extra_docs, context, answer, q_type)
                break

        if not winner:
            return (
                "⚠️ Hệ thống đã thử nhiều tài liệu liên quan nhưng vẫn không tìm được "
                "câu trả lời có căn cứ pháp lý khớp với tài liệu truy xuất. "
                "Vui lòng đặt lại câu hỏi cụ thể hơn."
            )

        best_doc, extra_docs, context, answer, q_type = winner

        # STEP 8.2: splice in "Căn cứ pháp lý" — built the same way as the
        # citation footer below (best_doc/extra_docs metadata only), never
        # written by the LLM itself (see build_legal_basis_line docstring).
        legal_basis = build_legal_basis_line(best_doc.metadata, extra_docs, best_doc.page_content, answer)
        if legal_basis:
            marker = "**Phân tích:**"
            idx = answer.find(marker)
            if idx != -1:
                answer = answer[:idx] + f"**Căn cứ pháp lý:** {legal_basis}\n" + answer[idx:]

        # STEP 9: citation — built strictly from best_doc/extra_docs metadata,
        # never from article numbers appearing in the LLM-generated answer.
        final_answer = answer + build_citation(best_doc.metadata, extra_docs, best_doc.page_content, answer)

        if return_debug:
            return {
                "answer": final_answer,
                "retrieved_context": context,
                "metadata": best_doc.metadata,
                "question_type": q_type,
            }

        return final_answer

    except Exception as e:
        print("RAG ERROR:", e)
        return "❌ Lỗi hệ thống."


# Runs once at import time, at the true bottom of the module so every name it
# needs (_ENTITY_AGNOSTIC_DOC_TYPES, detect_entity_type) is already bound —
# see the note next to the earlier startup-call block above.
backfill_entity_type_tags()