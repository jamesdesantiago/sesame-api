-- adds a public/private flag to all lists
ALTER TABLE lists
  ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;
