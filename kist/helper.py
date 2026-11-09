"""`kist helper`: lend another machine's CPU/GPU (a Mac) to the AI backlog.

    kist helper --url https://kist.example.com [--kinds embed,ocr] [--batch 16]

The token comes from KIST_HELPER_TOKEN (or --token, which other users of
the machine can see in the process list). The helper claims jobs over
HTTP, downloads each item's thumbnail, runs the same ONNX models the ml
service runs (on the CPU by default; --device coreml uses the Neural Engine) and posts the vector
or text back; the server checks it and writes the sidecar files. A helper
that stops mid-batch loses nothing: its jobs go back to the queue when
their lease runs out.

Needs the model extras on the helper machine: pip install 'kist[ml]'.
"""

import logging
import os
import platform
import signal
import socket
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger("kist.helper")


def default_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "kist-ml"


class Helper:
    def __init__(self, url: str, token: str, *, name: str, kinds: list[str], batch: int,
                 device: str, threads: int, cache_dir: Path, idle_sleep: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "X-Kist-Helper": name,
                     "User-Agent": "kist-helper"},
            timeout=120, transport=transport, follow_redirects=True,
        )
        self.name = name
        self.kinds = kinds
        self.batch = batch
        self.device = device
        self.threads = threads
        self.cache_dir = cache_dir
        self.idle_sleep = idle_sleep
        self.clip = None
        self.ocr = None
        self.stopping = False
        self.done = 0

    def _engines(self, model: str):
        from .ml.engine import ClipEngine, EngineConfig, OcrEngine

        if self.clip is None or self.clip.model != model:
            cfg = EngineConfig(model=model, cache_dir=self.cache_dir, threads=self.threads,
                               device=self.device, idle_seconds=0)
            self.clip = ClipEngine(cfg)
            self.ocr = OcrEngine(cfg)
        return self.clip, self.ocr

    def claim(self) -> tuple[str, list[dict]]:
        r = self.http.post("/api/ai/jobs/claim", json={
            "kinds": self.kinds, "max": self.batch, "name": self.name,
            "info": {"host": platform.node(), "platform": platform.platform(),
                     "device": self.device},
        })
        r.raise_for_status()
        body = r.json()
        return body["model"], body["jobs"]

    def run_job(self, model: str, job: dict) -> None:
        clip, ocr = self._engines(model)
        t0 = time.perf_counter()
        try:
            img = self.http.get(job["image_url"])
            img.raise_for_status()
            if job["kind"] == "embed":
                result = {"vector": clip.embed_image(img.content), "model": clip.model}
            else:
                out = ocr.ocr(img.content)
                result = {"text": out["text"], "model": out["model"], "lang": out["lang"],
                          "boxes": out["boxes"]}
        except Exception as exc:  # report it; the server retries or fails the job
            log.warning("job %s (%s) failed here: %s", job["id"], job["kind"], exc)
            result = {"error": f"{type(exc).__name__}: {exc}"[:3000]}
        r = self.http.post(f"/api/ai/jobs/{job['id']}/result", json=result)
        if r.status_code >= 400:
            log.warning("job %s: the server refused the result (%s): %s",
                        job["id"], r.status_code, r.text[:300])
            return
        self.done += 1
        log.debug("job %s %s in %.0f ms", job["id"], job["kind"],
                  (time.perf_counter() - t0) * 1000)

    def run(self, once: bool = False) -> int:
        while not self.stopping:
            try:
                model, jobs = self.claim()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    log.error("the server refused the token (%s)", exc.response.status_code)
                    return 2
                log.warning("claim failed: %s", exc)
                jobs, model = [], ""
            except httpx.HTTPError as exc:
                log.warning("server unreachable: %s", exc)
                jobs, model = [], ""
            if jobs:
                t0 = time.perf_counter()
                for job in jobs:
                    if self.stopping:
                        break  # unreported jobs go back to the queue after the lease
                    self.run_job(model, job)
                dt = time.perf_counter() - t0
                log.info("%d jobs in %.1f s (%.0f ms each); %d done so far",
                         len(jobs), dt, dt / len(jobs) * 1000, self.done)
                continue
            if once:
                return 0
            time.sleep(self.idle_sleep)
        return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="kist helper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="the Kist server, e.g. https://kist.example.com")
    p.add_argument("--token", default=None,
                   help="a helper token (default: $KIST_HELPER_TOKEN, safer than this flag)")
    p.add_argument("--name", default=socket.gethostname().split(".")[0],
                   help="how the server lists this helper (default: the host name)")
    p.add_argument("--kinds", default="embed,ocr", help="job kinds to take (embed,ocr)")
    p.add_argument("--batch", type=int, default=16, help="jobs claimed at a time")
    p.add_argument("--device", default=os.environ.get("KIST_ML_DEVICE", "cpu"),
                   help="cpu (default; fastest overall on Apple silicon for ViT-B-32) | "
                        "coreml (Neural Engine) | auto | openvino-gpu")
    p.add_argument("--threads", type=int, default=int(os.environ.get("KIST_ML_THREADS", "4")))
    p.add_argument("--cache", type=Path,
                   default=Path(os.environ.get("KIST_ML_CACHE") or default_cache()),
                   help="where model files are kept (default ~/.cache/kist-ml)")
    p.add_argument("--once", action="store_true", help="stop when the queue is empty")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per request otherwise
    token = args.token or os.environ.get("KIST_HELPER_TOKEN") or ""
    if not token:
        print("set KIST_HELPER_TOKEN (or pass --token)", file=sys.stderr)
        return 2
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        print("the helper needs the model libraries: pip install 'kist[ml]'", file=sys.stderr)
        return 2
    helper = Helper(args.url, token, name=args.name,
                    kinds=[k.strip() for k in args.kinds.split(",") if k.strip()],
                    batch=max(1, min(args.batch, 64)), device=args.device,
                    threads=args.threads, cache_dir=args.cache)

    def stop(*_):
        helper.stopping = True
        log.info("stopping after the current job")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log.info("helper %s working for %s (%s, device %s)", args.name, args.url, args.kinds,
             args.device)
    return helper.run(once=args.once)
