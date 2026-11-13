# AI search and captions

Kist understands what's in your images, on your own hardware. Nothing is sent
anywhere unless you choose a cloud caption provider.

- **Search by meaning.** Every item with a thumbnail gets an OpenCLIP
  ViT-B-32 embedding, so "brown suede boots" or "moody dark interiors" finds
  pictures of them, untagged. "More like this" and search by image (drop a
  picture on the search field; it isn't stored) use the same vectors.
- **Text in images.** Screenshots and other images without camera data are
  read by RapidOCR; PDFs give up their text layer, and only scanned pages are
  OCR'd. The words are found by the normal search and by `text=`.
- **Colour.** Every item has a five-colour palette. Press a colour in the info
  panel, or pick one in the search field, to find items with it.
- **Plain files.** Every result is written next to its item (`ai/`) before it
  goes into Postgres, so `kist rebuild-index` restores AI results in minutes and
  never recomputes them.

The `ml` service runs the models with ONNX Runtime on the CPU: no torch, no
GPU. It downloads ~600 MB of model files on first start (pinned, checked by
sha256), keeps about 1.2 GB of memory while working and unloads idle models
after 5 minutes.

## For the items you already have

```bash
docker compose exec worker kist ai-backfill      # embeddings + OCR for everything
curl -H "Authorization: Bearer $KIST_TOKEN" http://localhost:8080/api/ai/status
```

New items are done as they arrive. Backfill jobs wait for `KIST_AI_WINDOW`
(e.g. `01:00-06:00`) and for imports and previews.

| machine | embeddings | 100,000 items |
|---|---|---|
| Apple M-series, 4 threads (measured) | 80 images/s from one client | well under an hour |
| Intel i5-1235U NAS, 4 threads (estimated) | 15–25 images/s | 1.5–2.5 hours |

OCR is slower: about 0.4 s per screenshot on Apple silicon, 1.5–2.5 s on the
NAS. A Mac can take the backlog off a NAS with `kist helper` (a token with the
`helper` scope; see the README).

## Captions (optional)

A sentence or two per image, for search and alt text. Off by default; set
`KIST_CAPTION_PROVIDER`:

- `ollama`: nothing leaves your network. Run Ollama with a vision model
  (`qwen2.5vl:3b`, 3.2 GB) and set `KIST_CAPTION_URL` and
  `KIST_CAPTION_MODEL`. On a NAS CPU it takes tens of seconds per image, so
  caption on request, or let a Mac running `kist helper --kinds caption` do it.
- `openai` pointed at a server on your own network (LM Studio, vLLM) with
  `KIST_CAPTION_URL`: images stay at home.
- `openai`, `anthropic` or `gemini` with your own key: **every image you
  caption is uploaded to that company**, and Kist says so in
  `/api/ai/status` and the worker log.

Caption one item from its info panel (or `POST /api/items/{id}/caption`), all
of them with `kist ai-backfill --kind caption`, or new ones automatically with
`KIST_CAPTION_AUTO=true`. Captions are editable and undoable like any change.

## Faces (optional)

`KIST_FACES=1` for the app, worker and ml service turns on faces and people.
Only then does the ml service download the face models (~39 MB). Faces are
never matched against anything outside your library.

## Subject cut-outs

"Cut out subject" (preview, info panel, phone item sheet, the selection's
More menu, a board's inspector, MCP `cut_out_subject`) queues a `cutout` job
on the AI lane. The worker sends a preview-sized copy to the ml service
(`POST /cutout`), gets the subject's mask back and builds a transparent PNG
from the original's own pixels and colour profile, trimmed to the subject. It
becomes a new item linked *derived from* the original, with its tags, folders
and provenance; the original is never changed. Settings stored with it
(`media.cutout` in its `meta.json`): model, edge feather, keep shadow, the
crop box.

| model | licence | size | 1024 px on an Intel N100 | on an Apple M6 |
|---|---|---|---|---|
| `isnet-general-use` ("Fast", default) | Apache-2.0 | 179 MB | 3.3–5.2 s | 0.7–1.3 s |
| `birefnet-lite` ("Fine edges") | MIT | 224 MB | 23–29 s | 4–7 s |

Each model downloads the first time someone picks it (never at start), in
the background: until it's there, jobs wait without using up an attempt and
the dialog shows the progress. `KIST_CUTOUTS=0` turns cut-outs off.
`kist helper` doesn't run cut-out jobs; the ml service does.

## Tag suggestions

The info panel suggests tags from your own vocabulary, with the reason. They
are suggestions only: nothing is tagged until you (or an agent) accept one.
