-- Flowledger schema v4. Account/category/tag names are their identities.
-- Integer cents avoid floating-point rounding; one flow changes both endpoints.
PRAGMA application_id = 1179406167;
PRAGMA user_version = 4;

CREATE TABLE accounts (
    account_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    opening_cents INTEGER CHECK(opening_cents IS NULL OR typeof(opening_cents) = 'integer'),
    opening_at TEXT NOT NULL
);
CREATE TABLE categories (category_key TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE tags (tag_key TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO categories VALUES ('uncategorized', 'Uncategorized');

CREATE TABLE flows (
    flow_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    from_key TEXT NOT NULL REFERENCES accounts(account_key),
    to_key TEXT NOT NULL REFERENCES accounts(account_key),
    amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents) = 'integer' AND amount_cents > 0),
    status TEXT NOT NULL CHECK(status IN ('Complete', 'Pending', 'Planned', 'Void')),
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    category_key TEXT NOT NULL REFERENCES categories(category_key),
    CHECK(from_key <> to_key)
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
