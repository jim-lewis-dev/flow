-- Actual SQLite dump of the illustrative records in record_objects.json.
BEGIN TRANSACTION;
CREATE TABLE accounts (
    account_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    opening_cents INTEGER CHECK(opening_cents IS NULL OR typeof(opening_cents) = 'integer'),
    opening_at TEXT NOT NULL
);
INSERT INTO "accounts" VALUES('amex','AmEx',0,'2026-10-01 00:00:00');
INSERT INTO "accounts" VALUES('joe''s diner','Joe''s Diner',0,'2026-10-01 00:00:00');
CREATE TABLE audit (
    audit_id INTEGER PRIMARY KEY,
    changed_at TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT
);
INSERT INTO "audit" VALUES(1,'2026-10-02 20:37:50.763649','account','AmEx','create',NULL,'{"name": "AmEx", "opening_at": "2026-10-01 00:00:00", "opening_cents": 0}');
INSERT INTO "audit" VALUES(2,'2026-10-02 20:37:50.763735','account','Joe''s Diner','create',NULL,'{"name": "Joe''s Diner", "opening_at": "2026-10-01 00:00:00", "opening_cents": 0}');
INSERT INTO "audit" VALUES(3,'2026-10-02 20:37:50.764140','flow','11111111-1111-4111-8111-111111111111','create',NULL,'{"amount_cents": 1200, "category": "Dining", "date": "2026-10-02 12:30:00", "flow_id": "11111111-1111-4111-8111-111111111111", "from_account": "AmEx", "name": "Friday lunch", "notes": "Burger and fries", "status": "Complete", "tags": ["Road trip"], "to_account": "Joe''s Diner"}');
CREATE TABLE categories (category_key TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO "categories" VALUES('uncategorized','Uncategorized');
INSERT INTO "categories" VALUES('dining','Dining');
CREATE TABLE flow_tags (
    flow_id TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,
    tag_key TEXT NOT NULL REFERENCES tags(tag_key),
    position INTEGER NOT NULL,
    PRIMARY KEY(flow_id, tag_key)
);
INSERT INTO "flow_tags" VALUES('11111111-1111-4111-8111-111111111111','road trip',0);
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
INSERT INTO "flows" VALUES('11111111-1111-4111-8111-111111111111','2026-10-02 12:30:00','amex','joe''s diner',1200,'Complete','Friday lunch','Burger and fries','dining');
CREATE TABLE tags (tag_key TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO "tags" VALUES('road trip','Road trip');
CREATE INDEX flows_date ON flows(datetime(date), flow_id);
CREATE INDEX flows_from ON flows(from_key, date);
CREATE INDEX flows_to ON flows(to_key, date);
COMMIT;
