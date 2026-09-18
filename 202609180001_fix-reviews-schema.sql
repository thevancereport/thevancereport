-- Fixes the mismatch between the `reviews` table and netlify/functions/reviews.mjs.
--
-- The original migration (202609150001_create-reviews.sql) created three
-- criteria columns -- useful_signal / clear_analysis / actionable_format,
-- constrained to 'like' | 'needs_work'. The function was later rewritten
-- against a different set of five criteria constrained to 'good' |
-- 'needs_work', each with a paired free-text note. None of the columns the
-- function SELECTs or INSERTs exist, so every request to /api/reviews
-- errors -- which is what surfaces on the site as "Reviews are temporarily
-- unavailable."
--
-- Safe to run whether the table is absent, present with the old schema, or
-- already on the new one. Old rows are NOT remapped onto the new criteria:
-- three ratings cannot be turned into five without inventing two of them,
-- so any existing rows are preserved in reviews_legacy_20260918 for you to
-- read, and the live table starts clean.

BEGIN;

-- 1. Archive an old-schema table rather than dropping or mangling it.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'reviews' AND column_name = 'useful_signal'
  ) THEN
    ALTER INDEX IF EXISTS reviews_positive_score_created_idx
      RENAME TO reviews_legacy_20260918_score_idx;
    ALTER TABLE reviews RENAME TO reviews_legacy_20260918;
    RAISE NOTICE 'Old-schema reviews table archived as reviews_legacy_20260918.';
  END IF;
END $$;

-- 2. Create the table the function actually queries.
CREATE TABLE IF NOT EXISTS reviews (
  id BIGSERIAL PRIMARY KEY,

  data_freshness TEXT NOT NULL CHECK (data_freshness IN ('good', 'needs_work')),
  screen_filters TEXT NOT NULL CHECK (screen_filters IN ('good', 'needs_work')),
  site_layout    TEXT NOT NULL CHECK (site_layout    IN ('good', 'needs_work')),
  visual_design  TEXT NOT NULL CHECK (visual_design  IN ('good', 'needs_work')),
  info_quality   TEXT NOT NULL CHECK (info_quality   IN ('good', 'needs_work')),

  -- Nullable by design: the handler nulls these out for any criterion rated
  -- 'good', and requires 2-500 chars for any rated 'needs_work'.
  data_freshness_note TEXT CHECK (data_freshness_note IS NULL OR char_length(data_freshness_note) BETWEEN 2 AND 500),
  screen_filters_note TEXT CHECK (screen_filters_note IS NULL OR char_length(screen_filters_note) BETWEEN 2 AND 500),
  site_layout_note    TEXT CHECK (site_layout_note    IS NULL OR char_length(site_layout_note)    BETWEEN 2 AND 500),
  visual_design_note  TEXT CHECK (visual_design_note  IS NULL OR char_length(visual_design_note)  BETWEEN 2 AND 500),
  info_quality_note   TEXT CHECK (info_quality_note   IS NULL OR char_length(info_quality_note)   BETWEEN 2 AND 500),

  comment    TEXT NOT NULL CHECK (char_length(comment) BETWEEN 2 AND 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Index matching the public query: positive_score DESC, then created_at DESC.
CREATE INDEX IF NOT EXISTS reviews_positive_created_idx ON reviews (
  (
    (data_freshness = 'good')::int +
    (screen_filters = 'good')::int +
    (site_layout    = 'good')::int +
    (visual_design  = 'good')::int +
    (info_quality   = 'good')::int
  ) DESC,
  created_at DESC
);

COMMIT;

-- Verify afterwards:
--   SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_name = 'reviews' ORDER BY ordinal_position;
--
-- Expect 13 columns: id, the five criteria, the five *_note columns,
-- comment, created_at. Any archived rows:
--   SELECT count(*) FROM reviews_legacy_20260918;
