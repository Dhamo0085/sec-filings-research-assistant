from pathlib import Path
from typing import Dict, List, Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

COMPANIES: List[Dict[str, str]] = [
    {"ticker": "AAPL",  "name": "Apple Inc.",                   "sector": "Technology"},
    {"ticker": "MSFT",  "name": "Microsoft Corporation",        "sector": "Technology"},
    {"ticker": "GOOGL", "name": "Alphabet Inc.",                "sector": "Technology"},
    {"ticker": "AMZN",  "name": "Amazon.com Inc.",              "sector": "Technology"},
    {"ticker": "JPM",   "name": "JPMorgan Chase & Co.",         "sector": "Banking"},
    {"ticker": "WFC",   "name": "Wells Fargo & Company",        "sector": "Banking"},
    {"ticker": "BAC",   "name": "Bank of America Corporation",  "sector": "Banking"},
    {"ticker": "GS",    "name": "The Goldman Sachs Group Inc.", "sector": "Banking"},
    {"ticker": "BLK",   "name": "BlackRock Inc.",               "sector": "Asset Management"},
    {"ticker": "STT",   "name": "State Street Corporation",     "sector": "Asset Management"},
    {"ticker": "TROW",  "name": "T. Rowe Price Group Inc.",     "sector": "Asset Management"},
    {"ticker": "IVZ",   "name": "Invesco Ltd.",                 "sector": "Asset Management"},
]

TICKER_TO_COMPANY: Dict[str, Dict[str, str]] = {
    c["ticker"]: c for c in COMPANIES
}

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # silently ignore unknown env vars
    )

    # ── Secrets (P1-03) ──────────────────────────────────────────────────
    # These are OPTIONAL at import time and required only where they are
    # actually used, via require_groq_api() / require_edgar_email() below.
    #
    # v1 declared them mandatory, so `settings = Settings()` at the bottom of
    # this module raised pydantic ValidationError on `import config` whenever
    # .env was absent — which made the whole package unimportable, broke
    # offline tests, and was measured in Phase 0 (spec issue N4).
    groq_api: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("groq_api", "GROQ_API_KEY"),
    )
    edgar_email: Optional[str] = None
    gemini_api_key: Optional[str] = None

    # Optional shared secret for the admin routes. Unset DISABLES them
    # (fail-closed, D13) — enforced in api/auth.py from P1-06. v1 treated an
    # unset token as "no authentication required", which left every
    # /admin/* route open; Phase 0 confirmed that on the old deployment.
    admin_token: Optional[str] = None

    # Paths
    data_dir:       Path = BASE_DIR / "data"
    raw_dir:        Path = BASE_DIR / "data" / "raw"
    parsed_dir:     Path = BASE_DIR / "data" / "parsed"
    test_sets_dir:  Path = BASE_DIR / "data" / "test_sets"
    chunks_dir:     Path = BASE_DIR / "data" / "chunks"
    qdrant_path:    str  = str(BASE_DIR / "data" / "qdrant")
    # When set, get_client() connects to this remote Qdrant instance (Qdrant
    # Cloud or a self-hosted server) instead of embedded local-mode storage
    # under qdrant_path. See retrieval/vector_store.get_client() — local
    # mode loads/scans every collection into the API process's own memory,
    # which doesn't scale on a memory-capped host once you're past a
    # handful of collections.
    qdrant_url:     Optional[str] = None
    qdrant_api_key: Optional[str] = None
    # Set MODEL_CACHE_DIR to a persistent path in a hosted notebook to
    # persist the 219 MB embedding model across runtime restarts.
    model_cache_dir: Optional[Path] = None

    # Ingestion
    filing_type:          str = "10-K"
    filings_per_company:  int = 3
    # Parse/chunk ProcessPoolExecutor size. Each worker is a whole separate
    # Python process (lxml/BeautifulSoup imports and all) — os.cpu_count()
    # reflects the HOST's core count, not what a container is actually
    # allocated, so sizing off it on a memory-capped host (containers, etc.)
    # spawns far more processes than the container can hold at once and
    # exhausts it within seconds. Defaults to sequential (1); raise via the
    # PARSE_WORKERS env var on a host known to have room (local dev, notebooks).
    parse_workers: int = 1

    # Chunking
    max_chunk_tokens:      int = 1000
    chunk_overlap_sentences: int = 2

    # Embeddings  (fastembed / ONNX — no PyTorch required)
    embedding_model:     str = "BAAI/bge-base-en-v1.5" #change to a larger model if you have a GPU with enough VRAM
    embedding_dim:       int = 768
    embedding_batch_size: int = 64
    sparse_model:        str = "Qdrant/bm25"

    # Retrieval
    retrieval_top_k: int = 10
    rerank_top_k:    int = 3
    reranker_model:  str = "Xenova/ms-marco-MiniLM-L-12-v2"  # ONNX port via fastembed — no PyTorch

    # Legacy single-provider model names (Groq only). Kept because v1's
    # classifier/decomposer/generator/synthesizer and the parser's fs-heading
    # recovery still read them; P1-04 replaces their use with the ordered
    # provider lists below. NOTE: both defaults are Enterprise-only on Groq and
    # are NOT available to a free-tier key (measured in P1-00), so they must be
    # overridden via GENERATION_MODEL / ROUTING_MODEL.
    generation_model: str = "llama-3.3-70b-versatile"
    routing_model:    str = "llama-3.1-8b-instant"

    # ── LLM provider selection (spec section 8 / 9) ──────────────────────
    # Ordered "provider:model" failover lists. Per-role lists override
    # llm_providers. Populated by the P1-04 bake-off.
    llm_providers:       Optional[str] = None
    router_providers:    Optional[str] = None
    generator_providers: Optional[str] = None
    judge_providers:     Optional[str] = None

    llm_cache_path:          Path = BASE_DIR / ".cache" / "llm_cache.sqlite"
    llm_daily_token_budget:  Optional[int] = None   # None = use llm/limits.local.yaml

    # ── API limits and CORS (enforced in P1-06) ──────────────────────────
    cors_origins:        str = ""      # comma-separated allow-list; empty = same-origin only
    rate_limit_per_min:  int = 20
    max_question_chars:  int = 500

    # ── Derived stores (Phase 1 catalog, Phase 2 facts) ──────────────────
    catalog_path:  Path = BASE_DIR / "data" / "derived" / "catalog.sqlite"
    facts_db_path: Path = BASE_DIR / "data" / "derived" / "facts.sqlite"
    facts_filings_per_company: int = 5          # D15

    # ── Ablation switches (Phase 4 flips these; defaults are the full system)
    enable_facts:       bool = True
    enable_asof:        bool = True
    enable_abstain_gate: bool = True
    retrieval_mode:     str = "hybrid"          # hybrid | dense | bm25
    enable_focus_boost: bool = True
    enable_rerank:      bool = True
    facts_llm_phrasing: bool = False            # D8: deterministic templates by default

    @property
    def cors_origin_list(self) -> List[str]:
        """CORS_ORIGINS parsed into a list; empty means same-origin only."""
        return [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]

    # Dashboard env-var inputs (PaaS dashboards, ...) commonly end up with a
    # trailing newline or extra whitespace from copy-paste — invisible in the
    # UI, but any of these get embedded directly in an HTTP header (User-
    # Agent for SEC EDGAR, Authorization for Groq), and a bare "\n" in a
    # header value is rejected outright by the HTTP client with an opaque
    # InvalidHeader error. Strip defensively at the source instead of
    # depending on every caller/platform to paste cleanly.
    @field_validator("groq_api", "edgar_email", "admin_token", "qdrant_url",
                     "qdrant_api_key", "gemini_api_key", mode="after")
    @classmethod
    def _strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v

class MissingSettingError(RuntimeError):
    """A setting that is optional at import time was needed but is not set.

    Raised at the point of USE so that `import config` never fails (P1-03,
    spec issue N4) while a genuinely missing secret still fails loudly rather
    than silently producing a broken client (spec principle 4, "fail loud").
    """

    def __init__(self, setting_name: str, hint: str = "") -> None:
        self.setting_name = setting_name
        msg = f"Required setting '{setting_name}' is not set."
        if hint:
            msg += f" {hint}"
        super().__init__(msg)


settings = Settings()  # pyright: ignore[reportCallIssue]


def require_groq_api() -> str:
    """Return the Groq API key, or raise MissingSettingError."""
    if not settings.groq_api:
        raise MissingSettingError(
            "groq_api",
            "Set groq_api (or GROQ_API_KEY) in .env or the environment.",
        )
    return settings.groq_api


def require_edgar_email() -> str:
    """Return the SEC EDGAR contact address, or raise MissingSettingError.

    SEC fair-access requires a contact-shaped User-Agent; requests to
    www.sec.gov are rejected with HTTP 403 without one (observed in Phase 0).
    """
    if not settings.edgar_email:
        raise MissingSettingError(
            "edgar_email",
            "SEC EDGAR requires a contact email in the User-Agent header.",
        )
    return settings.edgar_email


def require_gemini_api_key() -> str:
    """Return the Gemini API key, or raise MissingSettingError."""
    if not settings.gemini_api_key:
        raise MissingSettingError(
            "gemini_api_key",
            "Set GEMINI_API_KEY in .env or the environment.",
        )
    return settings.gemini_api_key
