-- TigerData / TimescaleDB schema for live banking sync (idempotent DDL).

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    balance NUMERIC(18, 2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_accounts_customer_id ON accounts (customer_id);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    amount NUMERIC(18, 2) NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL,
    counterparty_account_id TEXT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (transaction_id, posted_at)
);

CREATE INDEX IF NOT EXISTS idx_transactions_customer_posted
    ON transactions (customer_id, posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_transactions_account_posted
    ON transactions (account_id, posted_at DESC);

CREATE TABLE IF NOT EXISTS customer_settings (
    customer_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TimescaleDB hypertable on posted_at (no-op if already converted).
SELECT create_hypertable(
    'transactions',
    'posted_at',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- account_id is the primary key, so a second customer cannot insert another
-- row. This trigger rejects UPDATEs that would move the existing row.
CREATE OR REPLACE FUNCTION accounts_reject_customer_reassign()
RETURNS trigger
LANGUAGE plpgsql
AS $accounts_owner$
BEGIN
    IF NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN
        RAISE EXCEPTION 'account_id % is owned by customer %', OLD.account_id, OLD.customer_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$accounts_owner$;

DROP TRIGGER IF EXISTS accounts_customer_owner ON accounts;

CREATE TRIGGER accounts_customer_owner
    BEFORE UPDATE OF customer_id ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION accounts_reject_customer_reassign();
