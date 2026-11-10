"""Search: item_colors (Lab palette rows kept by a trigger), CIEDE2000 in
SQL, captions in item_text, font items get previews

Revision ID: 0201
Revises: 0101
Create Date: 2026-11-09

"""

from alembic import op

revision = "0201"
down_revision = "0101"
branch_labels = None
depends_on = None

# kist/filetypes.FONT_MIMES (font files get a specimen preview from M2 on)
FONT_MIMES = (
    "font/ttf", "font/otf", "font/woff", "font/woff2", "font/sfnt", "font/collection",
    "application/font-woff", "application/font-woff2", "application/font-sfnt",
    "application/x-font-ttf", "application/x-font-otf", "application/x-font-truetype",
    "application/x-font-opentype", "application/vnd.ms-opentype",
)

# The same maths as kist/services/colour.py (tests compare the two).
HEX_LAB = r"""
CREATE OR REPLACE FUNCTION kist_hex_lab(hex text) RETURNS float8[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
    h text := lower(ltrim(coalesce(hex, ''), '#'));
    rgb float8[];
    c float8;
    i int;
    x float8; y float8; z float8;
    fx float8; fy float8; fz float8;
BEGIN
    IF h ~ '^[0-9a-f]{3}$' THEN
        h := substr(h,1,1) || substr(h,1,1) || substr(h,2,1) || substr(h,2,1)
             || substr(h,3,1) || substr(h,3,1);
    END IF;
    IF h !~ '^[0-9a-f]{6}$' THEN
        RETURN NULL;
    END IF;
    rgb := ARRAY[
        ('x' || substr(h, 1, 2))::bit(8)::int,
        ('x' || substr(h, 3, 2))::bit(8)::int,
        ('x' || substr(h, 5, 2))::bit(8)::int
    ]::float8[];
    FOR i IN 1..3 LOOP
        c := rgb[i] / 255.0;
        rgb[i] := CASE WHEN c <= 0.04045 THEN c / 12.92 ELSE ((c + 0.055) / 1.055) ^ 2.4 END;
    END LOOP;
    x := (0.4124564 * rgb[1] + 0.3575761 * rgb[2] + 0.1804375 * rgb[3]) / 0.95047;
    y := 0.2126729 * rgb[1] + 0.7151522 * rgb[2] + 0.0721750 * rgb[3];
    z := (0.0193339 * rgb[1] + 0.1191920 * rgb[2] + 0.9503041 * rgb[3]) / 1.08883;
    fx := CASE WHEN x > 216.0 / 24389 THEN cbrt(x) ELSE (24389.0 / 27 * x + 16) / 116 END;
    fy := CASE WHEN y > 216.0 / 24389 THEN cbrt(y) ELSE (24389.0 / 27 * y + 16) / 116 END;
    fz := CASE WHEN z > 216.0 / 24389 THEN cbrt(z) ELSE (24389.0 / 27 * z + 16) / 116 END;
    RETURN ARRAY[116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
END
$$;
"""

CIEDE2000 = r"""
CREATE OR REPLACE FUNCTION kist_ciede2000(
    l1 float8, a1 float8, b1 float8, l2 float8, a2 float8, b2 float8
) RETURNS float8
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE
    c1 float8 := sqrt(a1 * a1 + b1 * b1);
    c2 float8 := sqrt(a2 * a2 + b2 * b2);
    cbar7 float8 := ((c1 + c2) / 2) ^ 7;
    g float8 := 0.5 * (1 - sqrt(cbar7 / (cbar7 + 6103515625.0)));
    a1p float8 := (1 + g) * a1;
    a2p float8 := (1 + g) * a2;
    c1p float8 := sqrt(a1p * a1p + b1 * b1);
    c2p float8 := sqrt(a2p * a2p + b2 * b2);
    h1p float8;
    h2p float8;
    dlp float8 := l2 - l1;
    dcp float8;
    dhp float8;
    dbhp float8;
    lbp float8 := (l1 + l2) / 2;
    cbp float8;
    hbp float8;
    t float8;
    dtheta float8;
    cbp7 float8;
    rc float8;
    sl float8;
    sc float8;
    sh float8;
    rt float8;
BEGIN
    h1p := CASE WHEN c1p = 0 THEN 0 ELSE atan2d(b1, a1p) END;
    IF h1p < 0 THEN h1p := h1p + 360; END IF;
    h2p := CASE WHEN c2p = 0 THEN 0 ELSE atan2d(b2, a2p) END;
    IF h2p < 0 THEN h2p := h2p + 360; END IF;
    dcp := c2p - c1p;
    IF c1p * c2p = 0 THEN
        dhp := 0;
    ELSIF abs(h2p - h1p) <= 180 THEN
        dhp := h2p - h1p;
    ELSIF h2p - h1p > 180 THEN
        dhp := h2p - h1p - 360;
    ELSE
        dhp := h2p - h1p + 360;
    END IF;
    dbhp := 2 * sqrt(c1p * c2p) * sind(dhp / 2);
    cbp := (c1p + c2p) / 2;
    IF c1p * c2p = 0 THEN
        hbp := h1p + h2p;
    ELSIF abs(h1p - h2p) <= 180 THEN
        hbp := (h1p + h2p) / 2;
    ELSIF h1p + h2p < 360 THEN
        hbp := (h1p + h2p + 360) / 2;
    ELSE
        hbp := (h1p + h2p - 360) / 2;
    END IF;
    t := 1 - 0.17 * cosd(hbp - 30) + 0.24 * cosd(2 * hbp) + 0.32 * cosd(3 * hbp + 6)
         - 0.20 * cosd(4 * hbp - 63);
    dtheta := 30 * exp(-(((hbp - 275) / 25) ^ 2));
    cbp7 := cbp ^ 7;
    rc := 2 * sqrt(cbp7 / (cbp7 + 6103515625.0));
    sl := 1 + 0.015 * (lbp - 50) ^ 2 / sqrt(20 + (lbp - 50) ^ 2);
    sc := 1 + 0.045 * cbp;
    sh := 1 + 0.015 * cbp * t;
    rt := -sind(2 * dtheta) * rc;
    RETURN sqrt(greatest(0,
        (dlp / sl) ^ 2 + (dcp / sc) ^ 2 + (dbhp / sh) ^ 2 + rt * (dcp / sc) * (dbhp / sh)));
END
$$;
"""

SYNC_TRIGGER = r"""
CREATE OR REPLACE FUNCTION kist_item_colors_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.dominant_colors IS NOT DISTINCT FROM OLD.dominant_colors THEN
        RETURN NEW;
    END IF;
    DELETE FROM item_colors WHERE item_id = NEW.id;
    IF NEW.dominant_colors IS NOT NULL AND jsonb_typeof(NEW.dominant_colors) = 'array' THEN
        INSERT INTO item_colors (item_id, idx, l, a, b, pct)
        SELECT NEW.id, (e.ord - 1)::int, lab[1], lab[2], lab[3],
               CASE WHEN jsonb_typeof(e.value -> 'pct') = 'number'
                    THEN (e.value ->> 'pct')::float8 ELSE 0 END
        FROM jsonb_array_elements(NEW.dominant_colors) WITH ORDINALITY AS e(value, ord),
             LATERAL (SELECT kist_hex_lab(e.value ->> 'hex') AS lab) AS c
        WHERE jsonb_typeof(e.value) = 'object' AND c.lab IS NOT NULL;
    END IF;
    RETURN NEW;
END
$$;
"""


def upgrade() -> None:
    op.execute(HEX_LAB)
    op.execute(CIEDE2000)
    op.execute(
        """
        CREATE TABLE item_colors (
            item_id varchar(26) NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            idx integer NOT NULL,
            l real NOT NULL,
            a real NOT NULL,
            b real NOT NULL,
            pct real NOT NULL,
            PRIMARY KEY (item_id, idx)
        )
        """
    )
    # the colour search's bounding-box prefilter
    op.execute("CREATE INDEX ix_item_colors_lab ON item_colors (a, b, l)")
    op.execute(SYNC_TRIGGER)
    op.execute(
        "CREATE TRIGGER items_colors_sync AFTER INSERT OR UPDATE OF dominant_colors "
        "ON items FOR EACH ROW EXECUTE FUNCTION kist_item_colors_sync()"
    )
    # existing palettes: an UPDATE that changes nothing doesn't fire the
    # trigger's work, so fill the table directly
    op.execute(
        """
        INSERT INTO item_colors (item_id, idx, l, a, b, pct)
        SELECT i.id, (e.ord - 1)::int, lab[1], lab[2], lab[3],
               CASE WHEN jsonb_typeof(e.value -> 'pct') = 'number'
                    THEN (e.value ->> 'pct')::float8 ELSE 0 END
        FROM items i,
             jsonb_array_elements(i.dominant_colors) WITH ORDINALITY AS e(value, ord),
             LATERAL (SELECT kist_hex_lab(e.value ->> 'hex') AS lab) AS c
        WHERE i.dominant_colors IS NOT NULL AND jsonb_typeof(i.dominant_colors) = 'array'
          AND jsonb_typeof(e.value) = 'object' AND c.lab IS NOT NULL
        """
    )

    # captions live next to OCR/PDF text (one row per item and source)
    op.execute("ALTER TABLE item_text DROP CONSTRAINT IF EXISTS item_text_source_check")
    op.execute(
        "ALTER TABLE item_text ADD CONSTRAINT item_text_source_check "
        "CHECK (source IN ('ocr', 'pdf', 'caption'))"
    )

    # font files were "other" (no preview) before; queue their specimen
    mimes = ", ".join(f"'{m}'" for m in FONT_MIMES)
    font_items = (
        f"(mime IN ({mimes}) OR (mime = 'application/octet-stream' AND "
        f"lower(ext) IN ('ttf', 'otf', 'woff', 'woff2', 'ttc')))"
    )
    op.execute(
        f"INSERT INTO jobs (kind, payload) SELECT 'regen_preview', "
        f"jsonb_build_object('item_id', id) FROM items "
        f"WHERE preview_status = 'none' AND {font_items}"
    )
    op.execute(
        f"UPDATE items SET preview_status = 'pending' "
        f"WHERE preview_status = 'none' AND {font_items}"
    )


def downgrade() -> None:
    op.execute("DELETE FROM item_text WHERE source = 'caption'")
    op.execute("ALTER TABLE item_text DROP CONSTRAINT IF EXISTS item_text_source_check")
    op.execute(
        "ALTER TABLE item_text ADD CONSTRAINT item_text_source_check "
        "CHECK (source IN ('ocr', 'pdf'))"
    )
    op.execute("DROP TRIGGER IF EXISTS items_colors_sync ON items")
    op.execute("DROP FUNCTION IF EXISTS kist_item_colors_sync()")
    op.execute("DROP TABLE IF EXISTS item_colors")
    op.execute("DROP FUNCTION IF EXISTS kist_ciede2000(float8, float8, float8, float8, float8, "
               "float8)")
    op.execute("DROP FUNCTION IF EXISTS kist_hex_lab(text)")
