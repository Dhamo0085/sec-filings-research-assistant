"""Facts engine (Phase 2): inline-XBRL extraction, storage, resolution, calculation.

Modules are imported lazily by callers rather than re-exported here: ``extract``
pulls in lxml, and ``store`` opens SQLite, so an eager package import would make
``import facts`` expensive for code that only needs one of them.
"""
