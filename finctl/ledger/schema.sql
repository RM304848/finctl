-- Finance OS ledger schema
--
-- Conventions that hold everywhere in this file:
--   * Money is INTEGER cents. There are no floats in the ledger. Rates and
--     percentages are REAL, because they are not money.
--   * Sign convention is income-positive / expense-negative. The Haushaltsbuch
--     uses the opposite convention; migration flips it on import.
--   * Dates are TEXT in ISO-8601 (YYYY-MM-DD). SQLite has no date type and ISO
--     strings sort correctly.
--   * Classification uses two independent axes: management (2 levels) and
--     tax. They never collapse into one tree. Which project an expense
--     belonged to is a hand decision and lives in config/geteilt_custom.yaml.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- accounts --

CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,          -- 'dkb-giro'
    display_name    TEXT NOT NULL,
    institution     TEXT NOT NULL,
    account_type    TEXT NOT NULL CHECK (account_type IN
                        ('giro','tagesgeld','depot','broker','loan','credit_card')),
    iban            TEXT,
    account_no      TEXT,                      -- as printed on statements
    currency        TEXT NOT NULL DEFAULT 'EUR',
    -- 'parsed'  = transaction-level PDF import
    -- 'summary' = not exploded; visible only via transfers from parsed accounts
    ingest_mode     TEXT NOT NULL CHECK (ingest_mode IN ('parsed','summary')),
    parser_profile  TEXT,                      -- required when ingest_mode='parsed'
    -- Defaults to the account id. Set it when the folder on disk is named
    -- differently, so a user's own foldering keeps working.
    statement_folder TEXT,
    -- Forecast flags any month whose *trough* falls below this. Guards against
    -- landing in Dispokredit; NULL means no warning for this account.
    dispo_threshold_cents INTEGER,
    opened_on       TEXT,
    closed_on       TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    CHECK (ingest_mode <> 'parsed' OR parser_profile IS NOT NULL)
);

-- ------------------------------------------------------------- statements --

-- One imported PDF. The reconciliation gate lives here: a statement is only
-- accepted when balance_start + sum(transactions) == balance_end.
CREATE TABLE IF NOT EXISTS statements (
    id              INTEGER PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    source_path     TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    -- Idempotent re-import: the same file can be dropped in repeatedly.
    file_sha256     TEXT NOT NULL UNIQUE,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    balance_start_cents INTEGER NOT NULL,
    balance_end_cents   INTEGER NOT NULL,
    parser_profile  TEXT NOT NULL,
    parser_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('imported','rejected')),
    -- Which date the printed balances follow, so the chain check knows whether
    -- the opening figure already contains entries value-dated earlier.
    reconcile_basis TEXT NOT NULL DEFAULT 'booking',
    -- Non-zero only on rejection; kept for the diff report.
    reconcile_delta_cents INTEGER NOT NULL DEFAULT 0,
    imported_at     TEXT NOT NULL,
    CHECK (period_end >= period_start)
);

CREATE INDEX IF NOT EXISTS ix_statements_account_period
    ON statements(account_id, period_start);

-- ----------------------------------------------------------- transactions --

-- Immutable facts. Nothing here is ever edited; corrections happen in splits.
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    statement_id    INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
    booking_date    TEXT NOT NULL,
    value_date      TEXT,
    amount_cents    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    counterparty    TEXT,
    -- Case-folded, punctuation-stripped, legal-form-normalized. Rule matching
    -- and counterparty clustering both key on this, never on the raw string.
    counterparty_norm TEXT,
    counterparty_iban TEXT,
    purpose         TEXT,                      -- Verwendungszweck
    customer_ref    TEXT,                      -- Kundenreferenz
    tx_type         TEXT,                      -- Lastschrift, Überweisung, ...
    raw_text        TEXT NOT NULL,             -- everything the parser saw
    seq_in_statement INTEGER NOT NULL,
    -- Distinguishes genuinely repeated transactions (two identical coffees on
    -- one day) from a duplicate import. Counts identical tuples within a date.
    occurrence_index INTEGER NOT NULL DEFAULT 0,
    dedup_hash      TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tx_account_date ON transactions(account_id, booking_date);
CREATE INDEX IF NOT EXISTS ix_tx_counterparty ON transactions(counterparty_norm);

-- ------------------------------------------------------------- taxonomies --

-- Axis 1: management. Exactly two levels -- parent_id NULL means Kategorie,
-- otherwise Subkategorie. Enforced in code, not by the schema.
CREATE TABLE IF NOT EXISTS mgmt_categories (
    id          TEXT PRIMARY KEY,              -- 'konsum/elektronik'
    parent_id   TEXT REFERENCES mgmt_categories(id),
    name        TEXT NOT NULL,
    -- The tax position this category implies, applied when none is chosen.
    -- Business parking is deductible and private parking is not, and the only
    -- thing distinguishing them is which category was picked -- so the tax
    -- treatment should follow from that rather than being remembered
    -- separately every time.
    default_tax_id TEXT,
    kind        TEXT NOT NULL CHECK (kind IN ('income','expense','transfer','neutral')),
    -- A fourth axis, and the reason it is a flag rather than a branch: KFZ-Steuer
    -- and KFZ-Versicherung are fixed costs AND mobility costs. Moving them into
    -- Versicherung to make the Fixkosten view complete would empty them out of
    -- Mobilität, where they also belong. So the tree keeps saying WHAT a cost is
    -- and this says WHETHER it is contractually fixed.
    fixkosten   INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1
);

-- Axis 3: tax. Deliberately unrelated to the management tree -- the same split
-- carries both without either distorting the other.
CREATE TABLE IF NOT EXISTS tax_categories (
    id          TEXT PRIMARY KEY,              -- 'anlage_v/erhaltungsaufwand'
    parent_id   TEXT REFERENCES tax_categories(id),
    name        TEXT NOT NULL,
    anlage      TEXT,                          -- 'V','N','KAP','SO','VORSORGE', NULL = privat
    form_line   TEXT,                          -- WISO / ELSTER line reference
    deductible  INTEGER NOT NULL DEFAULT 0,
    requires_property INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1
);

-- ------------------------------------------------------- properties, loans --

CREATE TABLE IF NOT EXISTS properties (
    id              TEXT PRIMARY KEY,          -- 'w82a'
    name            TEXT NOT NULL,
    address         TEXT,
    acquired_on     TEXT,
    -- Sets the §23 EStG ten-year window. A sale inside it is taxable, so this
    -- date decides whether a planned disposal costs money.
    spekulationsfrist_end TEXT,
    purchase_price_cents  INTEGER,
    incidental_costs_cents INTEGER,
    -- Equity actually put in (down payment + Kaufnebenkosten paid from cash).
    -- The denominator for yield and payback, so it must be real money in, not
    -- the purchase price.
    equity_cents    INTEGER,
    land_share_pct  REAL,                      -- excluded from the AfA base
    afa_rate_pct    REAL,                      -- §7 EStG: 2.0 / 2.5 / 3.0
    -- The AfA base as the tax return states it, where it is known.
    --
    -- Reconstructing it from price x (1 - land share) is a derivation; the
    -- figure the Finanzamt has already accepted is a fact, and the two do not
    -- have to agree -- notary and Grunderwerbsteuer go into the base, the land
    -- share comes out, and neither is visible in a bank statement.
    afa_base_cents  INTEGER,
    -- AfA on assets that are not the building (§7 Abs. 1 EStG: kitchen,
    -- furniture, fittings). It runs on its own, much shorter, useful life, so
    -- it cannot be folded into the building base at the building's rate --
    -- A property can claim a four-figure amount of it a year beside the
    -- building's own AfA.
    afa_extra_annual_cents INTEGER,
    afa_start       TEXT,
    status          TEXT CHECK (status IN
                        ('planning','construction','rented','vacant','sold')),
    planned_sale_on TEXT,
    sold_on         TEXT,
    sale_price_cents INTEGER
);

CREATE TABLE IF NOT EXISTS loans (
    id              TEXT PRIMARY KEY,          -- '001'
    name            TEXT NOT NULL,
    lender          TEXT,
    property_id     TEXT REFERENCES properties(id),
    servicing_account_id TEXT REFERENCES accounts(id),
    principal_cents INTEGER NOT NULL,
    start_date      TEXT NOT NULL,
    payment_day     INTEGER NOT NULL DEFAULT 1,
    sondertilgung_annual_max_cents INTEGER,
    active          INTEGER NOT NULL DEFAULT 1
);

-- ------------------------------------------------------------------ rules --

-- config/rules.yaml is the source of truth; this table is the compiled mirror,
-- so categorization can join and the audit trail can reference a row.
CREATE TABLE IF NOT EXISTS rules (
    id              TEXT PRIMARY KEY,
    priority        INTEGER NOT NULL DEFAULT 100,
    name            TEXT,
    definition_json TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    provenance      TEXT NOT NULL CHECK (provenance IN
                        ('handwritten','ai-suggested','token','builtin')),
    source_hash     TEXT,                      -- of rules.yaml at load time
    loaded_at       TEXT NOT NULL
);

-- ----------------------------------------------------------------- splits --

-- The unit of meaning. Every transaction has 1..n splits summing to its amount;
-- a multi-item purchase is one split per category. All reporting reads splits,
-- so "break this expense down" stops being a special case.
CREATE TABLE IF NOT EXISTS splits (
    id              INTEGER PRIMARY KEY,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL DEFAULT 0,
    amount_cents    INTEGER NOT NULL,
    mgmt_category_id TEXT REFERENCES mgmt_categories(id),
    tax_category_id TEXT REFERENCES tax_categories(id),
    property_id     TEXT REFERENCES properties(id),
    loan_id         TEXT REFERENCES loans(id),
    -- Counterparty account for an internal transfer, when it is one of ours.
    transfer_account_id TEXT REFERENCES accounts(id),
    note            TEXT,
    -- 'manual' is sticky: categorize --recompute rebuilds rule-derived splits
    -- from scratch and never touches these.
    source          TEXT NOT NULL CHECK (source IN
                        ('rule','manual','token','import','default')),
    -- SET NULL, not CASCADE: the rulebook is rewritten on every
    -- categorize, and a manual split must outlive the rule it once
    -- recorded rather than being deleted with it.
    rule_id         TEXT REFERENCES rules(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (transaction_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_splits_tx    ON splits(transaction_id);
CREATE INDEX IF NOT EXISTS ix_splits_mgmt  ON splits(mgmt_category_id);
CREATE INDEX IF NOT EXISTS ix_splits_tax   ON splits(tax_category_id);
CREATE INDEX IF NOT EXISTS ix_splits_prop  ON splits(property_id);

-- Free tags were a third axis here. Projects under Geteilt replace them, so
-- an existing ledger drops the table on its next init.
DROP TABLE IF EXISTS split_tags;

-- Audit trail: which rule produced which split, and when.
CREATE TABLE IF NOT EXISTS rule_applications (
    id          INTEGER PRIMARY KEY,
    split_id    INTEGER NOT NULL REFERENCES splits(id) ON DELETE CASCADE,
    rule_id     TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

-- ------------------------------------------------------------ other flows --

-- Prognose und Szenarien haben hier keine Tabellen mehr. Sie werden bei jedem
-- Aufruf aus config/ gerechnet -- loans.yaml, szenarien.yaml, das Medianbudget
-- -- und nie gespeichert. Acht Tabellen dafuer standen hier, alle mit null
-- Zeilen und null Codereferenzen: recurring_series, forecast_assumptions,
-- scenarios, scenario_events, investment_flows, property_segments,
-- loan_segments, monthly_aggregates. Ein Schema, das etwas verspricht, was
-- der Code nicht einloest, ist schlimmer als eines, das schweigt.

-- Manual balances for accounts that are not exploded into transactions.
CREATE TABLE IF NOT EXISTS balance_snapshots (
    id          INTEGER PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    as_of       TEXT NOT NULL,
    balance_cents INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    UNIQUE (account_id, as_of)
);

-- ------------------------------------------------------------ invariants --

-- SQLite cannot express a cross-row CHECK, so the split-sum invariant is a
-- view. `finctl validate` asserts it is empty, and categorize runs it after
-- every pass. A non-empty result is always a bug, never a warning.
--
-- An INNER JOIN, deliberately: a transaction with no splits at all is merely
-- uncategorized, not imbalanced, and belongs in v_review_queue instead. Only a
-- transaction whose existing splits fail to sum to its amount is a defect.
CREATE VIEW IF NOT EXISTS v_split_imbalance AS
SELECT  t.id                AS transaction_id,
        t.account_id,
        t.booking_date,
        t.amount_cents      AS transaction_cents,
        SUM(s.amount_cents) AS split_cents,
        t.amount_cents - SUM(s.amount_cents) AS delta_cents
FROM        transactions t
JOIN        splits s ON s.transaction_id = t.id
GROUP BY    t.id
HAVING      t.amount_cents <> SUM(s.amount_cents);

-- Transactions still awaiting a decision: no splits at all, or any split left
-- uncategorized. NOT EXISTS rather than a join, so a four-way split that is
-- missing one category appears once here, not four times.
CREATE VIEW IF NOT EXISTS v_review_queue AS
SELECT  t.*
FROM        transactions t
WHERE   NOT EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id)
   OR       EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id
                                            AND s.mgmt_category_id IS NULL);

-- Editable targets, overriding the documented defaults in config/*.yaml.
--
-- Deliberately NOT written back to the YAML: those files carry the reasoning
-- in comments -- why 10.000 of reserve, why a 4.600 salary floor -- and a YAML
-- writer drops every comment. So config stays the documented default, these
-- win where set, and both remain visible.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    note        TEXT,
    updated_at  TEXT NOT NULL
);

-- Es gibt keine schema_migrations-Tabelle. Sie stand hier und wurde nie
-- beschrieben: Schemaaenderungen laufen ueber _ADDED_COLUMNS in
-- finctl/ledger/db.py, also idempotente ALTERs bei jedem connect(). Eine
-- Versionstabelle, die niemand hochzaehlt, sagt aus, dass es ein
-- Migrationssystem gaebe, und das waere gelogen.
