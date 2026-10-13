-- Additive upgrade for existing databases; sync fills metadata on the next import.
ALTER TABLE game ADD COLUMN IF NOT EXISTS name VARCHAR;
