-- Replaces the original three-criteria reviews table with the five-criteria
-- schema that netlify/functions/reviews.mjs actually queries.
--
-- The original migration (202609150001_create-reviews.sql, at the repo root and
-- never placed in this directory) created useful_signal / clear_analysis /
-- actionable_format with values 'like' | 'needs_work'. The handler was later
-- rewritten against data_freshness / screen_filters / site_layout /
-- visual_design / info_quality with values 'good' | 'needs_work', each with a
-- paired free-text note. Not one column overlapped, so every request to
-- /api/reviews threw -- surfacing on the site as "Reviews are temporarily
-- unavailable."
--
-- Nothing is deleted. The old table is renamed aside, so its contents stay
-- queryable at reviews_legacy_20260918. The rename is guarded on the old schema
-- being present, so a re-run against the new table is a no-op and cannot touch
-- real review data.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'reviews' AND column_name = 'useful_signal'
  ) THEN
    ALTER INDEX IF EXISTS reviews_positive_score_created_idx
      RENAME TO reviews_legacy_20260918_score_idx;
    ALTER TABLE reviews RENAME TO reviews_legacy_20260918;
    RAISE NOTICE 'Old-schema reviews table renamed to reviews_legacy_20260918.';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS reviews (
  id BIGSERIAL PRIMARY KEY,

  data_freshness TEXT NOT NULL CHECK (data_freshness IN ('good', 'needs_work')),
  screen_filters TEXT NOT NULL CHECK (screen_filters IN ('good', 'needs_work')),
  site_layout    TEXT NOT NULL CHECK (site_layout    IN ('good', 'needs_work')),
  visual_design  TEXT NOT NULL CHECK (visual_design  IN ('good', 'needs_work')),
  info_quality   TEXT NOT NULL CHECK (info_quality   IN ('good', 'needs_work')),

  -- Nullable by design: the handler nulls these for any criterion rated 'good',
  -- and requires 2-500 chars for any rated 'needs_work'.
  data_freshness_note TEXT CHECK (data_freshness_note IS NULL OR char_length(data_freshness_note) BETWEEN 2 AND 500),
  screen_filters_note TEXT CHECK (screen_filters_note IS NULL OR char_length(screen_filters_note) BETWEEN 2 AND 500),
  site_layout_note    TEXT CHECK (site_layout_note    IS NULL OR char_length(site_layout_note)    BETWEEN 2 AND 500),
  visual_design_note  TEXT CHECK (visual_design_note  IS NULL OR char_length(visual_design_note)  BETWEEN 2 AND 500),
  info_quality_note   TEXT CHECK (info_quality_note   IS NULL OR char_length(info_quality_note)   BETWEEN 2 AND 500),

  comment    TEXT NOT NULL CHECK (char_length(comment) BETWEEN 2 AND 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
