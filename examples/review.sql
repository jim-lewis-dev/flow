-- Actual SQLite dump of the illustrative records in record_objects.json.
PRAGMA application_id = 1179406167;
PRAGMA user_version = 6;
BEGIN TRANSACTION;
CREATE TABLE account_balances (
    account_key TEXT NOT NULL REFERENCES accounts(account_key),
    units TEXT NOT NULL CHECK(typeof(units) = 'text' AND length(trim(units)) > 0),
    opening_amount TEXT CHECK(opening_amount IS NULL OR typeof(opening_amount) = 'text'),
    opening_at TEXT NOT NULL,
    PRIMARY KEY(account_key, units)
);
INSERT INTO "account_balances" VALUES('amex','USD','0','2026-10-01 00:00:00');
INSERT INTO "account_balances" VALUES('warehouse','USD','1000','2026-10-01 00:00:00');
INSERT INTO "account_balances" VALUES('warehouse','kg','20','2026-10-01 00:00:00');
INSERT INTO "account_balances" VALUES('joe''s diner','USD','0','2026-10-01 00:00:00');
INSERT INTO "account_balances" VALUES('workshop','kg','0','2026-10-01 00:00:00');
CREATE TABLE accounts (
    account_key TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
INSERT INTO "accounts" VALUES('amex','AmEx');
INSERT INTO "accounts" VALUES('warehouse','Warehouse');
INSERT INTO "accounts" VALUES('joe''s diner','Joe''s Diner');
INSERT INTO "accounts" VALUES('workshop','Workshop');
CREATE TABLE audit (
    audit_id INTEGER PRIMARY KEY,
    changed_at TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT
);
INSERT INTO "audit" VALUES(1,'2026-10-10 15:31:37.311628','account','AmEx [USD]','create',NULL,'{"name": "AmEx", "opening_amount": "0", "opening_at": "2026-10-01 00:00:00", "units": "USD"}');
INSERT INTO "audit" VALUES(2,'2026-10-10 15:31:37.312512','account','Warehouse [USD]','create',NULL,'{"name": "Warehouse", "opening_amount": "1000", "opening_at": "2026-10-01 00:00:00", "units": "USD"}');
INSERT INTO "audit" VALUES(3,'2026-10-10 15:31:37.312585','account','Warehouse [kg]','create',NULL,'{"name": "Warehouse", "opening_amount": "20", "opening_at": "2026-10-01 00:00:00", "units": "kg"}');
INSERT INTO "audit" VALUES(4,'2026-10-10 15:31:37.312630','account','Joe''s Diner [USD]','create',NULL,'{"name": "Joe''s Diner", "opening_amount": "0", "opening_at": "2026-10-01 00:00:00", "units": "USD"}');
INSERT INTO "audit" VALUES(5,'2026-10-10 15:31:37.312666','account','Workshop [kg]','create',NULL,'{"name": "Workshop", "opening_amount": "0", "opening_at": "2026-10-01 00:00:00", "units": "kg"}');
INSERT INTO "audit" VALUES(6,'2026-10-10 15:31:37.313157','flow','11111111-1111-4111-8111-111111111111','create',NULL,'{"amount": "12", "category": "Dining", "date": "2026-10-02 12:30:00", "flow_id": "11111111-1111-4111-8111-111111111111", "from_account": "AmEx", "name": "Friday lunch", "notes": "Burger and fries", "status": "Complete", "tags": ["Road trip"], "to_account": "Joe''s Diner", "units": "USD"}');
INSERT INTO "audit" VALUES(7,'2026-10-10 15:31:37.313676','flow','22222222-2222-4222-8222-222222222222','create',NULL,'{"amount": "0.125", "category": "Materials", "date": "2026-10-03 09:15:06", "flow_id": "22222222-2222-4222-8222-222222222222", "from_account": "Warehouse", "name": "Materials", "notes": "Exact fractional quantity", "status": "Complete", "tags": ["Project"], "to_account": "Workshop", "units": "kg"}');
CREATE TABLE categories (category_key TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO "categories" VALUES('uncategorized','Uncategorized');
INSERT INTO "categories" VALUES('dining','Dining');
INSERT INTO "categories" VALUES('materials','Materials');
CREATE TABLE flow_tags (
    flow_id TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,
    tag_key TEXT NOT NULL REFERENCES tags(tag_key),
    position INTEGER NOT NULL,
    PRIMARY KEY(flow_id, tag_key)
);
INSERT INTO "flow_tags" VALUES('11111111-1111-4111-8111-111111111111','road trip',0);
INSERT INTO "flow_tags" VALUES('22222222-2222-4222-8222-222222222222','project',0);
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
    CHECK(from_key <> to_key),
    FOREIGN KEY(from_key, units) REFERENCES account_balances(account_key, units),
    FOREIGN KEY(to_key, units) REFERENCES account_balances(account_key, units)
);
INSERT INTO "flows" VALUES('11111111-1111-4111-8111-111111111111','2026-10-02 12:30:00','amex','joe''s diner','12','USD','Complete','Friday lunch','Burger and fries','dining');
INSERT INTO "flows" VALUES('22222222-2222-4222-8222-222222222222','2026-10-03 09:15:06','warehouse','workshop','0.125','kg','Complete','Materials','Exact fractional quantity','materials');
CREATE TABLE tags (tag_key TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO "tags" VALUES('road trip','Road trip');
INSERT INTO "tags" VALUES('project','Project');
CREATE INDEX flows_date ON flows(datetime(date), flow_id);
CREATE INDEX flows_from ON flows(from_key, date);
CREATE INDEX flows_to ON flows(to_key, date);
COMMIT;
