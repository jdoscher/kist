"""The model files Kist uses, pinned by revision and sha256.

The CLIP models are Immich's ONNX exports of OpenCLIP models on Hugging Face
(MIT-licensed exports; the OpenAI CLIP weights are MIT, SigLIP2 Apache-2.0).
They come down on first use into the cache directory (the `ml` container's
model volume, or ~/.cache/kist-ml for `kist helper`) and every file is
checked against its sha256 before it's used, so a changed or truncated
download is refused instead of silently giving different vectors.

RapidOCR's PP-OCRv4 models ship inside the rapidocr-onnxruntime wheel.

Only the tower a request needs is downloaded: the worker's embedding jobs
need the visual tower; the text tower (and its tokenizer) is fetched the
first time someone searches by meaning.
"""

import hashlib
import logging
import os
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("kist.ml.models")

DEFAULT_MODEL = "ViT-B-32__openai"
DEFAULT_BASE_URL = "https://huggingface.co"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    revision: str
    dim: int
    # path in the repo -> (sha256, size in bytes), per tower
    visual: dict[str, tuple[str, int]] = field(default_factory=dict)
    textual: dict[str, tuple[str, int]] = field(default_factory=dict)
    common: dict[str, tuple[str, int]] = field(default_factory=dict)

    def files(self, tower: str) -> dict[str, tuple[str, int]]:
        return {**self.common, **(self.visual if tower == "visual" else self.textual)}

    def url(self, path: str, base: str = DEFAULT_BASE_URL) -> str:
        return f"{base.rstrip('/')}/{self.repo}/resolve/{self.revision}/{path}"


MODELS: dict[str, ModelSpec] = {
    # default: 512-d, ~350 MB visual + ~250 MB text tower (fp32)
    "ViT-B-32__openai": ModelSpec(
        name="ViT-B-32__openai",
        repo="immich-app/ViT-B-32__openai",
        revision="a857c8de2c07bbcfa6646adfcf31b798845afa1e",
        dim=512,
        common={
            "config.json": (
                "59a182a6dce0adeb303f3d72d73758d68ba29516d928dd92740465f498290409", 196),
        },
        visual={
            "visual/model.onnx": (
                "33a3df41ceef21acdf371af00f6dd0456ec1f9eba24d03a7720f9c3734e40859", 351613724),
            "visual/preprocess_cfg.json": (
                "c4fd008cd177c58a8fe107b099bac98913eaa6be6114dd59a4b45145c3c7673f", 197),
        },
        textual={
            "textual/model.onnx": (
                "b80cf0af751533a6712d92247f0ddc0c95208748bc59a1a27f33e67be6864e3b", 254193396),
            "textual/tokenizer.json": (
                "6d9109cc838977f3ca94a379eec36aecc7c807e1785cd729660ca2fc0171fb35", 3642073),
            "textual/tokenizer_config.json": (
                "39bae5313c2da6467866dee758e754d69f0e302a875027b142531cb9c51c7c28", 736),
        },
    ),
    # opt-in: 768-d, ~2.5x slower per image; its text tower is 1.1 GB
    "ViT-B-16-SigLIP2__webli": ModelSpec(
        name="ViT-B-16-SigLIP2__webli",
        repo="immich-app/ViT-B-16-SigLIP2__webli",
        revision="5f3fc29104ebcf51e9d893af57604badd51a3ea6",
        dim=768,
        common={
            "config.json": (
                "a33af509278a65855fd6b4fd8a17ed1b1e3482ff5d438c3fc43b07d4999e01b5", 547),
        },
        visual={
            "visual/model.onnx": (
                "fee10c729875dd203d94f396ac3d664e301d39c977ef6d92e7f467a2a716f0b0", 371674292),
            "visual/preprocess_cfg.json": (
                "d78a80d49dca4e62d17f802a685d67d821f7102844cb89f65a0bb1a5d0601f43", 154),
        },
        textual={
            "textual/model.onnx": (
                "fc9991b415d7d0fac7ed540e875fd08a647c8a1abba99f12542531b004c4b68f", 1129435819),
            "textual/tokenizer.json": (
                "220c63d496e0c14e63eb656c91e0215e926202e4c74b1f089e09f1920d779b04", 34362885),
            "textual/tokenizer_config.json": (
                "958146aa289a0a39e4fcb20087a78facb157d29c7ef017374ac3ccab0549aae4", 46386),
        },
    ),
}


@dataclass(frozen=True)
class FileSpec:
    """One model file outside Hugging Face, pinned by commit and sha256."""

    name: str
    url: str
    sha256: str
    size: int
    license: str = ""


# Faces (opt-in, KIST_FACES=1): OpenCV-zoo's YuNet and SFace, Apache-2.0,
# from github.com/opencv/opencv_zoo at a fixed commit (Git LFS files, served
# from media.githubusercontent.com)
OPENCV_ZOO = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "48b10fa505aeb51fbd127fc60a5c660040e823b7/models"
)
FACE_MODELS: dict[str, FileSpec] = {
    "detector": FileSpec(
        name="yunet-2023mar",
        url=f"{OPENCV_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        size=232589,
        license="MIT (YuNet) / Apache-2.0 (opencv_zoo)",
    ),
    "recognizer": FileSpec(
        name="sface-2021dec",
        url=f"{OPENCV_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        size=38696353,
        license="Apache-2.0",
    ),
}


@dataclass(frozen=True)
class CutoutSpec:
    """A background-removal model (F7 cutouts): one ONNX file pinned by
    revision and sha256, and how to feed it."""

    name: str
    url: str
    sha256: str
    size: int
    license: str
    side: int  # the square input it takes (pixels)
    # "isnet": x/max - 0.5, output already a probability, min-max stretched
    # "birefnet": x/255, ImageNet mean/std, output logits (sigmoid)
    kind: str
    # what the ml container needs for one run of it, with CLIP and OCR loaded
    # beside it (measured peak RSS on dev0, Nov 2026: ISNet 1.4 GB,
    # BiRefNet-lite 6.3 GB); below it the container is killed mid-cut-out
    needs_memory: int = 2 * 1024**3


# Cutouts (KIST_CUTOUTS, on by default; downloaded on the first cut-out a
# user asks for, each model on its own first use). Inference at 1024 px on
# 4 threads, measured 2026-11-12: ISNet 3.3-5.2 s on an Intel N100, 0.7-1.3 s
# on an Apple M6; BiRefNet-lite 23-29 s on the N100, 4-7 s on the M6.
# ISNet is the default ("fast"); BiRefNet-lite keeps thin parts (straps,
# antennas) and white-on-white subjects better and is the "fine" choice
# per cut-out. KIST_CUTOUT_MODEL changes the default.
CUTOUT_MODELS: dict[str, CutoutSpec] = {
    # DIS / IS-Net "general use" weights (github.com/xuebinqin/DIS,
    # Apache-2.0), the ONNX export rembg publishes (byte-identical to
    # github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx)
    "isnet-general-use": CutoutSpec(
        name="isnet-general-use",
        url="https://huggingface.co/jellybox/isnet-general-use/resolve/"
            "bc3706ce4ee38a3db2c3c58082a92582fcdfcfdc/isnet-general-use_1024.onnx",
        sha256="60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a",
        size=178648008,
        license="Apache-2.0",
        side=1024,
        kind="isnet",
    ),
    # BiRefNet lite (github.com/ZhengPeng7/BiRefNet, MIT), onnx-community's export
    "birefnet-lite": CutoutSpec(
        name="birefnet-lite",
        url="https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/"
            "de15b22ba131738a16dff04aab8bdf8dc32e3ac1/onnx/model.onnx",
        sha256="5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333",
        size=224005088,
        license="MIT",
        side=1024,
        kind="birefnet",
        needs_memory=8 * 1024**3,
    ),
}
DEFAULT_CUTOUT_MODEL = "isnet-general-use"


def cutout_path(spec: CutoutSpec, cache_dir: Path) -> Path:
    return cache_dir / "cutout" / spec.name / spec.sha256[:12] / "model.onnx"


def cutout_ready(spec: CutoutSpec, cache_dir: Path) -> bool:
    """Downloaded and checked already (no hashing here)."""
    return _is_verified(cutout_path(spec, cache_dir), spec.sha256, spec.size)


def ensure_cutout(spec: CutoutSpec, cache_dir: Path, base_url: str | None = None,
                  progress=None) -> Path:
    """Download (if needed) and verify a cutout model; returns its path.
    KIST_CUTOUT_MODELS_URL replaces the URL up to the file name (a mirror)."""
    dest = cutout_path(spec, cache_dir)
    if _is_verified(dest, spec.sha256, spec.size):
        return dest
    if dest.is_file() and dest.stat().st_size == spec.size and sha256_of(dest) == spec.sha256:
        _verified_marker(dest).write_text(spec.sha256)
        return dest
    url = spec.url
    mirror = base_url or os.environ.get("KIST_CUTOUT_MODELS_URL", "")
    if mirror:
        url = mirror.rstrip("/") + "/" + spec.url.rsplit("/", 1)[-1]
    _download(url, dest, spec.sha256, spec.size, progress=progress)
    return dest


class ModelError(RuntimeError):
    pass


def spec_for(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError:
        raise ModelError(
            f"unknown model {name!r}; Kist knows {', '.join(sorted(MODELS))}"
        ) from None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _verified_marker(path: Path) -> Path:
    return path.with_name(f".{path.name}.sha256")


def _is_verified(path: Path, sha: str, size: int) -> bool:
    """A file checked once is trusted while its size and marker agree (re-
    hashing 600 MB at every start would cost seconds)."""
    try:
        if path.stat().st_size != size:
            return False
        return _verified_marker(path).read_text().strip() == sha
    except OSError:
        return False


def ensure_files(spec: ModelSpec, tower: str, cache_dir: Path,
                 base_url: str = DEFAULT_BASE_URL) -> Path:
    """Download (if needed) and verify a tower's files; returns the model dir."""
    root = cache_dir / spec.name / spec.revision
    for rel, (sha, size) in spec.files(tower).items():
        dest = root / rel
        if _is_verified(dest, sha, size):
            continue
        if dest.is_file() and dest.stat().st_size == size and sha256_of(dest) == sha:
            _verified_marker(dest).write_text(sha)
            continue
        _download(spec.url(rel, base_url), dest, sha, size)
    return root


def ensure_file(spec: FileSpec, cache_dir: Path, base_url: str | None = None) -> Path:
    """Download (if needed) and verify one pinned file; returns its path.
    KIST_FACE_MODELS_URL replaces the opencv_zoo prefix (a mirror)."""
    dest = cache_dir / "faces" / spec.url.rsplit("/", 1)[-1]
    if _is_verified(dest, spec.sha256, spec.size):
        return dest
    if dest.is_file() and dest.stat().st_size == spec.size and sha256_of(dest) == spec.sha256:
        _verified_marker(dest).write_text(spec.sha256)
        return dest
    url = spec.url
    mirror = base_url or os.environ.get("KIST_FACE_MODELS_URL", "")
    if mirror:
        url = mirror.rstrip("/") + "/" + spec.url.rsplit("/", 1)[-1]
    _download(url, dest, spec.sha256, spec.size)
    return dest


def _download(url: str, dest: Path, sha: str, size: int, progress=None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s (%.0f MB)", url, size / 1e6)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    h = hashlib.sha256()
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kist-ml"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=60) as resp:
            while chunk := resp.read(4 * 1024 * 1024):
                h.update(chunk)
                out.write(chunk)
                got += len(chunk)
                if progress is not None:
                    progress(got)
        if got != size or h.hexdigest() != sha:
            raise ModelError(
                f"{url}: got {got} bytes with sha256 {h.hexdigest()}, expected {size} "
                f"bytes with sha256 {sha}; refusing to use it"
            )
        os.replace(tmp, dest)
        _verified_marker(dest).write_text(sha)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
