-- Migration 001: Initial Schema for Investment Intelligence Analyst

CREATE TABLE IF NOT EXISTS articles (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,                    -- e.g. 'macromicro', 'vocus_chivesking'
    url         TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    author      TEXT,
    published_at TIMESTAMPTZ,
    content     TEXT,
    ai_summary  TEXT,
    sentiment   TEXT CHECK (sentiment IN ('bullish', 'neutral', 'bearish')),
    tickers     TEXT[],                           -- related stock tickers
    version_of  BIGINT REFERENCES articles(id),  -- for superseded tracking
    status      TEXT DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS news_items (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT UNIQUE,
    title       TEXT NOT NULL,
    content     TEXT,
    published_at TIMESTAMPTZ,
    category    TEXT,                             -- e.g. 'macro', 'tw_stock', 'us_stock'
    tickers     TEXT[],
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    market      TEXT NOT NULL,                    -- e.g. 'TW', 'US', 'VIX'
    symbol      TEXT NOT NULL,
    close_price NUMERIC(12, 4),
    change_pct  NUMERIC(8, 4),
    volume      BIGINT,
    extra       JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (snapshot_date, market, symbol)
);

CREATE TABLE IF NOT EXISTS analysis_reports (
    id              BIGSERIAL PRIMARY KEY,
    report_date     DATE NOT NULL,
    macro_short     TEXT,                         -- 1-2 week outlook
    macro_mid       TEXT,                         -- 1-3 month outlook
    macro_long      TEXT,                         -- 3-12 month outlook
    top_themes      JSONB,                        -- [{theme, tickers, confidence}]
    raw_prompt      TEXT,
    raw_response    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchlist (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    market      TEXT NOT NULL CHECK (market IN ('TW', 'US')),
    name        TEXT,
    added_reason TEXT,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ticker, market)
);

-- Auto-update updated_at for articles
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER articles_updated_at
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
