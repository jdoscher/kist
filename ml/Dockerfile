# kist-ml: CLIP embeddings, OCR and (opt-in, KIST_FACES=1) faces for Kist,
# on ONNX Runtime (no torch).
#
# Build from the repository root (compose does this):
#   docker build -f ml/Dockerfile -t kist-ml .
# Intel GPU (OpenVINO, e.g. the Iris Xe in an i5-1235U), about +200 MB:
#   docker build -f ml/Dockerfile --build-arg OPENVINO=1 -t kist-ml .
#   (or KIST_ML_OPENVINO=1 docker compose build ml) and set
#   KIST_ML_DEVICE=openvino-gpu; see docker-compose.yml for /dev/dri.
#
# Model files are not in the image: they download on first start into
# /models (a volume), pinned by revision and checked by sha256
# (kist/ml/models.py). Set KIST_ML_BASE_URL to use a mirror. The face models
# (YuNet + SFace, ~39 MB) download the same way, and only with KIST_FACES=1
# (KIST_FACE_MODELS_URL for a mirror).
FROM python:3.12-slim-bookworm

ARG OPENVINO=0
ARG ORT_VERSION=1.31.1
ARG ORT_OPENVINO_VERSION=1.24.1

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp XDG_CACHE_HOME=/tmp/.cache \
    KIST_ML_CACHE=/models KIST_ML_THREADS=4 KIST_ML_DEVICE=cpu

# libgomp: OpenMP for ONNX Runtime/OpenCV. The OpenVINO build also needs
# Intel's OpenCL runtime to reach the GPU through /dev/dri.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && if [ "$OPENVINO" = "1" ]; then \
         apt-get install -y --no-install-recommends intel-opencl-icd ocl-icd-libopencl1; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ml/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt \
    && if [ "$OPENVINO" = "1" ]; then \
         pip install "onnxruntime-openvino==${ORT_OPENVINO_VERSION}"; \
       else \
         pip install "onnxruntime==${ORT_VERSION}"; \
       fi \
    && pip install --no-deps rapidocr-onnxruntime==1.4.4

# only the self-contained ml package (it imports nothing else from Kist)
COPY kist/__init__.py ./kist/__init__.py
COPY kist/ml/ ./kist/ml/

RUN groupadd --system --gid 1000 kist \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin kist \
    && mkdir -p /models && chown 1000:1000 /models
USER 1000:1000
VOLUME ["/models"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]
CMD ["uvicorn", "kist.ml.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
