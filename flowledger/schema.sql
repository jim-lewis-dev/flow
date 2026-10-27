-- Flowledger schema v7. Names identify accounts; units distinguish quantities.
-- Exact decimal text avoids rounding. Starting balances belong to account/unit pairs.
PRAGMA application_id = 1179406167;
PRAGMA user_version = 7;

CREATE TABLE accounts (
    account_key TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE account_balances (
    account_key TEXT NOT NULL REFERENCES accounts(account_key),
    units TEXT NOT NULL CHECK(typeof(units) = 'text' AND length(trim(units)) > 0),
    opening_amount TEXT CHECK(opening_amount IS NULL OR typeof(opening_amount) = 'text'),
    opening_at TEXT NOT NULL,
    PRIMARY KEY(account_key, units)
);
CREATE TABLE categories (category_key TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE tags (tag_key TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO categories VALUES ('uncategorized', 'Uncategorized');

CREATE TABLE flows (
    flow_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    from_key TEXT NOT NULL,
    to_key TEXT NOT NULL,
    amount TEXT NOT NULL CHECK(typeof(amount) = 'text' AND length(amount) > 0),
    units TEXT NOT NULL CHECK(typeof(units) = 'text' AND length(trim(units)) > 0),
    status TEXT NOT NULL CHECK(status IN ('Complete', 'Pending', 'Planned', 'Canceled')),
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    category_key TEXT NOT NULL REFERENCES categories(category_key),
    FOREIGN KEY(from_key, units) REFERENCES account_balances(account_key, units),
    FOREIGN KEY(to_key, units) REFERENCES account_balances(account_key, units)
);
CREATE INDEX flows_date ON flows(datetime(date), flow_id);
CREATE INDEX flows_from ON flows(from_key, date);
CREATE INDEX flows_to ON flows(to_key, date);

CREATE TABLE flow_tags (
    flow_id TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,
    tag_key TEXT NOT NULL REFERENCES tags(tag_key),
    position INTEGER NOT NULL,
    PRIMARY KEY(flow_id, tag_key)
);
CREATE TABLE audit (
    audit_id INTEGER PRIMARY KEY,
    changed_at TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT
);
