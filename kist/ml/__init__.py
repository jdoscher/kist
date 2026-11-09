"""Kist's local machine learning: CLIP-family image/text embeddings and OCR.

Self-contained on purpose: nothing here imports the rest of Kist (its
settings, database or decoders), so the `ml` image ships only this package
plus ONNX Runtime, and `kist helper` runs the very same code on a Mac.

    models.py   the pinned model files (URL, revision, sha256) and download
    engine.py   ONNX Runtime sessions, preprocessing, tokenizer, RapidOCR
    server.py   the FastAPI app the `ml` container serves
"""
