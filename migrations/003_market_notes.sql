-- Migration 003: Market notes JSON + market_summary column

ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_summary    TEXT;
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_notes_json JSONB;
