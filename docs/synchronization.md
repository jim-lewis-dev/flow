# Synchronization

Each device works on its own SQLite file. A coordinator receives individual
changes, puts them in order, and distributes them to the other devices. Local
saves work without the coordinator; unsent changes remain in the same file
until the connection returns.

Synchronization is optional for each dataset. Standalone files continue to work
without a service or network connection.

## Where things live

| Location | Contents | Purpose |
| --- | --- | --- |
| Each computer or phone | Local Flow file, received revisions, pending changes | Immediate saves and offline work |
| Coordinator computer, on its local disk | Coordinator database, certificate, key, connection settings | Order and distribute accepted changes |
| Router drive, optionally | One refreshed database snapshot | A separate copy; its availability does not control sync |

Run the service on a computer reachable by your other devices. A drive attached
to the router provides storage, not a place to run that service. Keep the live
coordinator database on that computer's local disk. Devices communicate with it
over HTTPS; they do not open its SQLite file over a network share.

Flows, starting balances, and saved account-map positions synchronize. Filters,
unfinished forms, selection, and session Undo history stay on their device.
Sync metadata lives in internal `sync_*` tables; the visible flow fields and
base schema remain unchanged.

Update both apps before connecting a file. Shared files reject writes from
older Flow versions that cannot record changes for sync. Ordinary standalone
files keep their existing compatibility.

## Start the coordinator

Use the same Flow source version on the coordinator and desktop clients. The
service needs Python 3.10 or newer and OpenSSL to create its certificate once;
it does not need Tkinter or Matplotlib.

First choose the current file that will seed the shared dataset. Finish edits
and close its application while transferring it to the coordinator computer.
Do not edit other copies until they have joined. In this example, the
transferred file is `~/money.sqlite3`; replace that path with yours.

From the Flow project directory on the coordinator computer:

```bash
python3 -m flowledger.sync_server init ~/money.sqlite3 \
  --directory ~/.local/share/flow-sync/money \
  --url https://192.168.1.50:8765

python3 -m flowledger.sync_server serve \
  --directory ~/.local/share/flow-sync/money
```

Replace `192.168.1.50` with the coordinator computer's reachable LAN address.
A fixed address or router DHCP reservation avoids changing it later. If a
firewall blocks the service, allow the chosen TCP port from your local network.

`init` creates a new service directory and leaves the source file unchanged.
It refuses to overwrite an existing directory. Run it once; use `serve` to
restart that same coordinator.

To maintain a snapshot on the router drive, add this option to **init**:

```bash
--mirror /mnt/shared/flow/money.sqlite3
```

Replace that path with a file on your mounted network drive and create its
parent folder first. The mirror replaces this one file after successful writes; it does not
accumulate numbered backups. It runs separately from sync and retries if the
share becomes unavailable. Treat the mirror as a snapshot, not as an editable
client file.

## Connect the devices

In another terminal on the coordinator computer, print the connection text:

```bash
python3 -m flowledger.sync_server connection \
  --directory ~/.local/share/flow-sync/money
```

The text contains the dataset identity, address, access key, and certificate
fingerprint. Paste it only into your Flow clients; it grants access to that
shared dataset. Keep it and the coordinator directory out of source control.
This version uses one access key per shared file, with no per-person roles or
permissions.

**Desktop:** open the original matching file, or a new empty file such as
`flow money`. Click **Sync**, paste the connection text, and click **Connect**.
An empty file receives the shared records. A nonempty file must match the seed;
Flow refuses to replace a different set of records. First connection starts a
fresh session Undo history.

**Android:** open **Files > Synchronization**, enter the local filename and
connection text, then choose **Join shared file**. A matching local file connects
in place; a new name creates a local copy. A different existing file is never
overwritten. The joined file lives in Flow's private phone folder and is available
offline. Existing files opened through Android's document picker retain their
manual-transfer behavior; join a local file for synchronization.

After joining, ordinary valid edits save locally and synchronize automatically.
Desktop checks about once a second; Android checks about every two seconds
while the app is in the foreground. Saving also requests a sync immediately;
leaving the phone app makes a best-effort final attempt. Reopening or returning catches
up. Android has no background synchronization service.

Before switching devices, **Up to date** confirms that the current device has
exchanged its saved changes with the coordinator. Offline or pending status
means that some changes may not yet be visible elsewhere.

## Conflicts and Undo

Different fields can change independently. If two devices change the same field
before receiving each other's update, Flow keeps the local edit and shows a
conflict. Choose **Keep local** or **Use server** on desktop; Android calls these
**Keep this device's edit** and **Use received change**. The choice applies to
the saved action, which may contain several related record changes. Later
queued actions remain available and may need review if they depended on it.

Each operation has a unique ID. Retrying after a lost reply cannot add a flow
twice. The coordinator checks field revisions and accepts operations in one
order; record dates do not determine synchronization order.

Undo and Redo belong to the current device's running session. Undo creates a
new inverse change, which can then synchronize. It stops if a newer received
change affected the same value, even if that value was changed back again.
Unrelated received fields are preserved. There are no field locks.

## When something is unavailable

| Situation | Result |
| --- | --- |
| Phone or computer leaves the network | Edits save locally and queue for the next connection |
| Coordinator is off | Every device can keep working; delivery waits for the same coordinator to return |
| Router drive is unavailable | Sync continues; the snapshot retries separately |
| App closes before receiving an acknowledgement | Saved operations remain queued and retry safely |
| Two devices edit the same field independently | Explicit conflict; no silent overwrite |

This setup has one coordinator, not automatic failover. Starting a second one
from an old file would create two competing histories. A temporary outage is
simpler: leave the local files alone and restart the existing service.

To move the service deliberately, stop the old service and copy its **entire
coordinator directory** to the new host. Preserve its database, certificate,
private key, and settings. Start that directory on the replacement host only.
Keeping the same network address avoids reconnecting clients. If the address
changes, update `endpoint` in `coordinator.json`, print the connection text
again, and reconnect clients to the same dataset: **Sync > Connect** on desktop,
or **Files > Synchronization > Update connection** on Android. These connection
updates retain pending local edits.

The router's database snapshot does not contain the service's certificate or
access key. It is not a complete service-directory backup. Do not replace a
running coordinator with an older snapshot: devices may already have received
revisions that the snapshot lacks.

## Run after login on Linux

After checking the service manually, a user service can restart it on login.
Create `~/.config/systemd/user/flow-sync-money.service` with the following,
adjusting the project path if needed:

```ini
[Unit]
Description=Flow synchronization for money

[Service]
WorkingDirectory=%h/projects/flow
ExecStart=/usr/bin/python3 -m flowledger.sync_server serve --directory %h/.local/share/flow-sync/money
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
```

Stop the manually started service, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now flow-sync-money
journalctl --user -u flow-sync-money -f
```

A user service normally runs while that user is logged in. If the coordinator must run
it from boot without a login, its administrator can enable lingering for that
user. This is a host configuration choice; Flow does not change it.
