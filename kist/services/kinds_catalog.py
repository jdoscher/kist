"""The kinds Kist sorts pictures into (F1 utility collections): what each one
is, how it is detected, and which pack switches it on.

A kind is detected by
- `prompts`: CLIP zero-shot. The prompts are averaged into one text vector
  and scored against `negatives` (plus GENERIC_NEGATIVES) with a softmax at
  CLIP's temperature: the probability that the picture is this rather than
  one of the alternatives;
- `rule`: a plain rule on the file (type, EXIF, size, OCR, provenance), in
  services/kinds.py. Rules are the calls we trust most;
- or both (the higher score wins, and a rule can veto: a photo with camera
  EXIF is never a screenshot).

`parent`: a child kind (Pencil & graphite under Drawings, scored against its
siblings only, so it has no `negatives`) is only given to
items that are in the parent, and only when CLIP is clearly surer of it than
of its siblings; an unsure call leaves the item in the parent alone. CLIP is
weak at telling drawing media apart (ECCV 2024), so the children start strict
and get better from corrections.

`learnable`: corrections train a small classifier on top of the zero-shot
score (services/kinds.py). Pure rule kinds (Photos, Low-res, AI-made) only
take corrections as verdicts.

Packs: "base" kinds are on by default; trade packs are off until switched on
in Settings. A kind in several packs (Renders & 3D) is on when any of them is.
"""

from dataclasses import dataclass, field

GENERIC_NEGATIVES = (
    "a photo of a person",
    "a photo of an object",
    "a landscape photo",
    "a photo of a room",
    "a photo of a street",
    "a photo of an animal",
    "a photo of food",
)


@dataclass(frozen=True)
class KindDef:
    id: str
    name: str
    packs: tuple[str, ...]
    prompts: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()
    rule: str | None = None
    parent: str | None = None
    learnable: bool = True
    default_strictness: float = 0.5
    # shown in Settings: how it's found, in plain words
    how: str = ""
    # opt-in kinds (AI-made) stay off even in their pack until switched on
    opt_in: bool = False
    extra: dict = field(default_factory=dict)


PACKS: dict[str, str] = {
    "base": "Everyone",
    "fashion": "Fashion",
    "architecture": "Architecture & product",
    "film": "Film & games",
    "editorial": "Editorial",
}

_PHOTO = ("a photograph", "a photo of a person", "a photo of an object")
_ART = ("a painting", "a pencil drawing", "an ink drawing", "a digital illustration")

KINDS: tuple[KindDef, ...] = (
    # ---------- base: on for everyone ----------
    KindDef(
        "screenshots", "Screenshots", ("base",),
        prompts=("a screenshot of a phone app", "a screenshot of a website",
                 "a screenshot of a computer screen", "a screenshot of a social media post"),
        negatives=("a photo of a computer screen", "a scanned page of text", *_PHOTO, *_ART),
        rule="screenshot",
        how="PNG without camera data at a screen's exact size, or named like a screenshot; "
            "CLIP 'a screenshot of …'",
    ),
    KindDef(
        "documents", "Documents & pages", ("base",),
        prompts=("a scanned page of text", "a photo of a printed document", "a product data sheet",
                 "a typed letter", "a page of a contract"),
        negatives=("a screenshot of a website", "a poster", "a book cover", *_PHOTO, *_ART),
        rule="document",
        how="PDFs, and pictures where OCR finds a page of words; CLIP 'a scanned page of text'",
    ),
    KindDef(
        "drawings", "Drawings", ("base",),
        prompts=("a drawing", "a pencil sketch on paper", "a pen and ink drawing",
                 "a hand-drawn sketch", "a charcoal drawing"),
        negatives=("a painting", "an oil painting", "a watercolor painting", "a photograph",
                   "a digital illustration", "a screenshot", "a scanned page of text",
                   "a 3D render"),
        how="CLIP 'a drawing', 'a pencil sketch', 'a pen and ink drawing'",
    ),
    KindDef(
        "pencil", "Pencil & graphite", ("base",),
        prompts=("a pencil sketch on paper", "a graphite drawing", "a pencil drawing with shading"),
        parent="drawings", default_strictness=0.6,
        how="Drawings that CLIP clearly reads as pencil rather than ink; learns from corrections",
    ),
    KindDef(
        "ink", "Ink & line art", ("base",),
        prompts=("a pen and ink drawing", "black ink line art", "an ink illustration",
                 "a brush and ink drawing"),
        parent="drawings", default_strictness=0.6,
        how="Drawings that CLIP clearly reads as ink rather than pencil; learns from corrections",
    ),
    KindDef(
        "paintings", "Paintings", ("base",),
        prompts=("an oil painting", "a watercolor painting", "a gouache painting",
                 "an acrylic painting on canvas", "a painting"),
        negatives=("a photograph", "a pencil drawing", "a pen and ink drawing",
                   "a digital illustration", "a print", "a screenshot", "a 3D render"),
        how="CLIP 'an oil painting', 'a watercolor painting' …",
    ),
    KindDef(
        "photos", "Photos (from a camera)", ("base",), rule="camera", learnable=False,
        how="Images with camera make or model in their EXIF data",
    ),
    KindDef(
        "low-res", "Low-res", ("base",), rule="low_res", learnable=False,
        how="Images under the size you set (long edge in pixels, or print size when known)",
    ),
    KindDef(
        "ai-made", "AI-made", ("base",), rule="ai_made", learnable=False, opt_in=True,
        how="Only what the file's own metadata says (C2PA, IPTC digital source type, the "
            "AI-made flag in provenance); never guessed from pixels",
    ),
    # ---------- Fashion ----------
    KindDef(
        "runway", "Runway", ("fashion",),
        prompts=("a model walking on a runway", "a fashion show catwalk photo",
                 "a runway look at a fashion show"),
        negatives=("a fashion editorial photo", "a street style photo", *_PHOTO),
        how="CLIP 'a model walking on a runway'",
    ),
    KindDef(
        "flats", "Technical flats", ("fashion",),
        prompts=("a technical flat drawing of a garment", "a fashion flat sketch",
                 "a front and back garment technical drawing"),
        negatives=("a fashion illustration", "a pencil sketch on paper", "a product photo",
                   *_PHOTO),
        how="CLIP 'a technical flat drawing of a garment'",
    ),
    KindDef(
        "product-on-white", "Product on white", ("fashion", "architecture"),
        prompts=("a product photo on a white background", "a packshot on a plain background",
                 "an e-commerce product photo"),
        negatives=("a product in a room", "a lifestyle photo", "a 3D render", *_ART),
        how="CLIP 'a product photo on a white background'",
    ),
    KindDef(
        "fabric", "Fabric close-ups", ("fashion",),
        prompts=("a close-up of fabric texture", "a macro photo of knit fabric",
                 "an embroidery detail", "a fabric swatch"),
        negatives=("a garment on a model", "a seamless repeating pattern", *_PHOTO),
        how="CLIP 'a close-up of fabric texture'",
    ),
    KindDef(
        "prints", "Prints & repeats", ("fashion",),
        prompts=("a seamless repeating pattern", "a floral print fabric", "a textile print design",
                 "a wallpaper pattern"),
        negatives=("a close-up of fabric texture", "a painting", *_PHOTO),
        how="CLIP 'a seamless repeating pattern'",
    ),
    KindDef(
        "colour-cards", "Colour cards", ("fashion",),
        prompts=("a colour swatch card", "a grid of paint colour chips", "a colour palette chart"),
        negatives=("a painting", "a screenshot of a website", *_PHOTO),
        rule="colour_card",
        how="OCR finds PANTONE, TCX or a colour code; CLIP 'a colour swatch card'",
    ),
    # ---------- Architecture & product ----------
    KindDef(
        "plans", "Plans, sections & elevations", ("architecture",),
        prompts=("an architectural floor plan", "an architectural section drawing",
                 "an architectural elevation drawing", "a blueprint", "a technical drawing"),
        negatives=("a pencil sketch of a building", "a photo of a building", "a 3D render",
                   "a map"),
        how="CLIP 'an architectural floor plan', 'a blueprint'",
    ),
    KindDef(
        "renders", "Renders & 3D", ("architecture", "film"),
        prompts=("a 3D render", "a CGI rendering", "an architectural visualization render",
                 "an Unreal Engine screenshot"),
        negatives=("a photograph", "a painting", "a pencil drawing", "a screenshot of a website"),
        how="CLIP 'a 3D render', 'CGI'",
    ),
    KindDef(
        "built-work", "Built work & site", ("architecture",),
        prompts=("a photo of a building exterior", "an architectural photograph",
                 "a photo of a construction site", "an interior architecture photo"),
        negatives=("a 3D render", "an architectural drawing", "a photo of a person",
                   "a landscape photo"),
        how="CLIP 'an architectural photograph', 'a construction site'",
    ),
    KindDef(
        "materials", "Material close-ups", ("architecture",),
        prompts=("a close-up of a material sample", "a close-up of wood grain",
                 "a close-up of marble stone", "a close-up of concrete texture",
                 "a close-up of brushed metal"),
        negatives=("a photo of a room", "a photo of a building", "a product photo"),
        how="CLIP 'a close-up of a material sample'. Families only (wood, stone); a species "
            "is never assigned",
    ),
    KindDef(
        "models", "Physical models", ("architecture",),
        prompts=("a physical architectural model", "a scale model of a building",
                 "a cardboard maquette", "a foam prototype model"),
        negatives=("a photo of a building", "a 3D render", "a toy"),
        how="CLIP 'a scale model of a building'",
    ),
    KindDef(
        "whiteboards", "Whiteboards & markups", ("architecture",),
        prompts=("a photo of a whiteboard with notes", "a marked-up drawing with red pen",
                 "sticky notes on a wall"),
        negatives=("a scanned page of text", "a pencil sketch on paper", "a screenshot"),
        how="CLIP 'a whiteboard with notes', 'a marked-up drawing'",
    ),
    # ---------- Film & games ----------
    KindDef(
        "film-stills", "Film & TV stills", ("film",),
        prompts=("a still frame from a movie", "a cinematic film still", "a frame from a TV show"),
        negatives=("a photograph", "a 3D render", "a screenshot of a website", *_ART),
        rule="film_still",
        how="Film-shaped frames (2.39, 1.85, 16:9) without camera data; CLIP 'a still frame "
            "from a movie'",
    ),
    KindDef(
        "storyboards", "Storyboards", ("film",),
        prompts=("a storyboard sketch", "a page of storyboard panels", "a comic book page"),
        negatives=("a pencil sketch on paper", "a scanned page of text", "a painting"),
        how="CLIP 'a page of storyboard panels'",
    ),
    KindDef(
        "textures", "Textures", ("film",),
        prompts=("a seamless texture", "a close-up texture of rust", "a tileable ground texture",
                 "a close-up texture of tree bark"),
        negatives=("a landscape photo", "a photo of an object", "a seamless repeating pattern"),
        how="CLIP 'a seamless texture'",
    ),
    KindDef(
        "hdris", "HDRIs & panoramas", ("film",),
        prompts=("an equirectangular 360 degree panorama", "an HDRI environment map"),
        negatives=("a landscape photo", "a wide photo"),
        rule="panorama",
        how="2:1 equirectangular images (and HDR/EXR files); CLIP '360 degree panorama'",
    ),
    KindDef(
        "colour-scripts", "Colour scripts", ("film",),
        prompts=("a colour script for an animated film", "a grid of small colour key paintings",
                 "a sequence of colour thumbnails"),
        negatives=("a painting", "a page of storyboard panels", "a colour swatch card"),
        how="CLIP 'a colour script', 'colour keys'",
    ),
    # ---------- Editorial ----------
    KindDef(
        "lettering", "Lettering & type", ("editorial",),
        prompts=("hand lettering", "a typographic poster", "a type specimen",
                 "a photo of shop signage lettering"),
        negatives=("a scanned page of text", "a logo", "a screenshot of a website"),
        how="CLIP 'hand lettering', 'a typographic poster'",
    ),
    KindDef(
        "logos", "Logos & marks", ("editorial",),
        prompts=("a logo on a plain background", "a brand mark", "an icon design"),
        negatives=("hand lettering", "a poster", "a screenshot of a website", *_PHOTO),
        how="CLIP 'a logo on a plain background'",
    ),
    KindDef(
        "printed-matter", "Printed matter", ("editorial",),
        prompts=("a magazine spread", "a book cover", "product packaging", "a printed poster",
                 "a magazine page"),
        negatives=("a scanned page of text", "a screenshot of a website", "a painting"),
        how="CLIP 'a magazine spread', 'a book cover', 'packaging'",
    ),
    KindDef(
        "relief-prints", "Relief & intaglio prints", ("editorial",),
        prompts=("a linocut print", "a woodcut print", "an etching", "an engraving"),
        negatives=("a pen and ink drawing", "a pencil sketch on paper", "a painting",
                   "a photograph"),
        how="CLIP 'a linocut print', 'an etching'",
    ),
)

BY_ID: dict[str, KindDef] = {k.id: k for k in KINDS}


def children(kind_id: str) -> list[str]:
    return [k.id for k in KINDS if k.parent == kind_id]
