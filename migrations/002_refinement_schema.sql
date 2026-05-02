-- Migration 002: Refinement pipeline + trading rankings + pgvector

-- Enable pgvector extension (Supabase supports this natively)
CREATE EXTENSION IF NOT EXISTS vector;

-- Add refinement fields to articles
ALTER TABLE articles ADD COLUMN IF NOT EXISTS refined_content TEXT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS content_tags   TEXT[];   -- ['macro','international','stock','supply_chain']
ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding      vector(768); -- multilingual-mpnet-base-v2

-- Index for vector similarity search
CREATE INDEX IF NOT EXISTS articles_embedding_idx
    ON articles USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS articles_source_published ON articles (source, published_at DESC);
CREATE INDEX IF NOT EXISTS articles_tickers ON articles USING GIN (tickers);
CREATE INDEX IF NOT EXISTS articles_tags ON articles USING GIN (content_tags);

-- Daily trading value rankings (top 30 per market per day)
CREATE TABLE IF NOT EXISTS trading_rankings (
    id              BIGSERIAL PRIMARY KEY,
    rank_date       DATE NOT NULL,
    market          TEXT NOT NULL CHECK (market IN ('US', 'TW', 'JP')),
    rank            INT  NOT NULL,
    ticker          TEXT NOT NULL,
    name            TEXT,
    trading_value   NUMERIC(20, 2),     -- total traded value in local currency
    close_price     NUMERIC(12, 4),
    change_pct      NUMERIC(8, 4),
    volume          BIGINT,
    is_limit_up_30m BOOLEAN DEFAULT FALSE,  -- TW: hit 漲停 in first 30 min
    extra           JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (rank_date, market, ticker)
);

CREATE INDEX IF NOT EXISTS trading_rankings_date_market ON trading_rankings (rank_date DESC, market);

-- Extend analysis_reports for full daily report format
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS macro_summary    TEXT;        -- 100-word macro summary
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS market_indicators JSONB;      -- {sp500, nasdaq, sox, nikkei, taiex, vix, yield_10y, fear_greed, dxy}
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS sentiment_short  TEXT CHECK (sentiment_short IN ('bullish','neutral','bearish'));
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS sentiment_mid    TEXT CHECK (sentiment_mid   IN ('bullish','neutral','bearish'));
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS sentiment_long   TEXT CHECK (sentiment_long  IN ('bullish','neutral','bearish'));
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS themes           JSONB;       -- [{theme, confidence, reasoning, tickers_us, tickers_tw}]
ALTER TABLE analysis_reports ADD COLUMN IF NOT EXISTS opportunities    JSONB;       -- [{ticker, market, reason, confidence, related_theme}]

-- Market indicators snapshot (replaces/extends market_snapshots for specific indicators)
-- Reuse existing market_snapshots with these convention symbols:
--   market='INDICATOR', symbol='VIX' | '10Y_YIELD' | 'FEAR_GREED' | 'DXY'
--   market='US', symbol='SPY' | 'QQQ' | 'SOXX'
--   market='TW', symbol='^TWII'
--   market='JP', symbol='^N225'
