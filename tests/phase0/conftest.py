"""
Phase 0 test support.

The audited checkout has no installed vector-store / embedding stack and no
``.env``.  Importing the real modules would therefore fail at import time for
reasons unrelated to what we are measuring.  To keep the existing source files
completely untouched (Phase 0 rule: read-only), we install lightweight stand-ins
in ``sys.modules`` *before* the modules under test are imported, and we inject
dummy settings via the environment.

Nothing here modifies the repository's own code.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# config.Settings requires these two; values are placeholders, never used to
# make a network call in Phase 0 unit tests.
os.environ.setdefault("groq_api", "phase0-dummy-key")
os.environ.setdefault("edgar_email", "phase0@example.com")


_INSTALLED: dict = {}


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def install_heavy_stubs() -> None:
    """Stub out qdrant_client / fastembed / nltk / tiktoken / pandas / numpy.

    retrieval.retriever -> retrieval.vector_store -> qdrant_client is the only
    reason those packages are needed at import time; every test monkeypatches
    the functions that would actually use them.
    """
    if _INSTALLED.get("done"):
        return
    _INSTALLED["done"] = True

    class _AnythingModule(types.ModuleType):
        """Module whose every attribute access yields a permissive dummy class."""

        def __getattr__(self, item):
            if item.startswith("__"):
                raise AttributeError(item)
            return type(item, (), {"__init__": lambda self, *a, **k: None})

    # Prefer the REAL qdrant-client when it is installed. test_k9_local_filter.py
    # needs genuine local-mode behaviour, and a stub that shadowed it would make
    # that test's result depend on which tests ran first.
    try:
        import qdrant_client  # noqa: F401
        import qdrant_client.models  # noqa: F401
    except ImportError:
        qc = _AnythingModule("qdrant_client")
        qc.QdrantClient = type("QdrantClient", (), {"__init__": lambda self, *a, **k: None})
        sys.modules["qdrant_client"] = qc
        sys.modules["qdrant_client.models"] = _AnythingModule("qdrant_client.models")
        sys.modules["qdrant_client.http"] = _AnythingModule("qdrant_client.http")

    _stub(
        "fastembed",
        TextEmbedding=type("TextEmbedding", (), {"__init__": lambda self, *a, **k: None}),
        SparseTextEmbedding=type("SparseTextEmbedding", (), {"__init__": lambda self, *a, **k: None}),
    )
    _stub("fastembed.rerank", )
    _stub(
        "fastembed.rerank.cross_encoder",
        TextCrossEncoder=type("TextCrossEncoder", (), {"__init__": lambda self, *a, **k: None}),
    )

    _stub(
        "sec_edgar_downloader",
        Downloader=type("Downloader", (), {"__init__": lambda self, *a, **k: None}),
    )

    # nltk: ingestion/chunker.py probes nltk.data.find() at IMPORT time and
    # calls nltk.download() on LookupError.  Stub it so importing query.py in
    # a test never touches the network.
    class _NltkData:
        @staticmethod
        def find(_path):
            return "/stubbed/path"

    _stub("nltk", data=_NltkData, download=lambda *a, **k: None,
          sent_tokenize=lambda t: [t])

    class _Enc:
        def encode(self, text):
            return text.split()

    _stub("tiktoken", get_encoding=lambda *a, **k: _Enc(),
          encoding_for_model=lambda *a, **k: _Enc())

    for heavy in ("numpy", "pandas"):
        if heavy not in sys.modules:
            try:
                __import__(heavy)
            except ImportError:
                sys.modules[heavy] = _AnythingModule(heavy)
