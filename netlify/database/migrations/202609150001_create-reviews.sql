CREATE TABLE reviews (
  id BIGSERIAL PRIMARY KEY,
  useful_signal TEXT NOT NULL CHECK (useful_signal IN ('like', 'needs_work')),
  clear_analysis TEXT NOT NULL CHECK (clear_analysis IN ('like', 'needs_work')),
  actionable_format TEXT NOT NULL CHECK (actionable_format IN ('like', 'needs_work')),
  comment TEXT NOT NULL CHECK (char_length(comment) BETWEEN 2 AND 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX reviews_positive_score_created_idx ON reviews (
  ((useful_signal = 'like')::int + (clear_analysis = 'like')::int + (actionable_format = 'like')::int) DESC,
  created_at DESC
);
