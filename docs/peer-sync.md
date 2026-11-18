# Share a Flow file

Each device works on a local SQLite file. Pair devices once, then pick the
files to share. Either device can create the original. No computer is in
charge. A standalone file, or a file using the older coordinator, stays that
way until you choose otherwise.

## Try it on a spare file

1. Connect both devices to the same LAN. Open a test file on the first device
   (for example, `flow peer-test` on Linux).
2. Open **Sync > Devices > Pair nearby** on desktop and **Files > Pair device >
   Pair nearby** on the phone. Tap the other device's name on one screen.
   Compare the number on the two screens. If it matches, choose **Numbers
   match** on both. Nothing needs to be typed. For an older peer, the manual
   code option is still available.
3. On the device holding the file, choose **Share this file** (desktop:
   **Sync > Shared files**; Android: **Files**). On the other, choose
   **Find shared files**, select the file, then join. Joining creates a local
   copy. A local `money` and a shared `money` can coexist; joining leaves
   the local file untouched.
4. Edit a flow. Leave both apps open and check the other device. To add a third
   device, pair it with one that already has the file and join there. Pair each
   pair of devices that should exchange changes directly. Pairing does not
   automatically trust friends of friends.

Display names are labels. Two independently created files called `money` are
different datasets. If you reinstall on a new phone, pair its new identity;
the old name alone cannot grant access. Paired devices can read and join every file that the other
device explicitly shares. Keep unrelated files local. Removing a device stops
future direct access from that installation; it cannot recall copies.

Flows, account starting balances, tags and saved node positions travel with
their changes. The undo stack, screen selections and audit log stay local.
Each shared file retains its edit history so a device can catch up later.
On Linux, device keys and the list of files you share live separately under
`~/.config/flow/peers/` (or `$XDG_CONFIG_HOME/flow/peers/`). Those settings
belong to that installation; do not copy them to another device.

A phone exchanges changes while Flow is in the foreground. A computer does so
while Flow or its optional service is running. If A meets B today and B meets
C tomorrow, C can learn A's changes even if A is off. If **no pair ever
overlaps**, nothing travels. Changes wait on disk. An idle network drive is
not a peer. Do not open the same live SQLite file on multiple devices.

If a name does not appear, check that both devices are in nearby pairing and
on the same home network. **Find at address** can use the other device's
Wi-Fi or Ethernet address, or a hostname your network resolves. It uses
the same number comparison; the port is optional. A timeout means Flow
could not complete the connection. Guest Wi-Fi isolation, a firewall, or a
VPN that blocks LAN access can prevent this. On Linux, `ip -br -4 addr`
shows which address belongs to each interface.

The peer listener uses TCP 47621; automatic discovery uses UDP 47622.
These are home-network ports. No router port forwarding is needed. If you
use UFW, scope any rules to your actual LAN. For example, **only if** your
LAN is `192.168.0.0/24` and your firewall is blocking Flow:

```sh
sudo ufw allow from 192.168.0.0/24 to any port 47621 proto tcp
sudo ufw allow from 192.168.0.0/24 to any port 47622 proto udp
```

After updating a desktop, reopen Flow. If the optional background peer
is already running, restart it with `systemctl --user restart flow-peer.service`
so it uses the updated pairing protocol. The older coordinator is a separate
service. Existing paired devices and shared files keep their identities.

Linux uses OpenSSL to create a device identity once; the phone uses Android.
The initial snapshot is limited to
56 MiB of serialized records. After joining, edits travel in batches of up to
2 MiB. There is no network transfer of the live SQLite file.

## What happens to conflicting edits?

Independent fields merge. If the last agreed amount was 20 Wh and two
disconnected devices change it to 25 Wh and 30 Wh, a device that knows both
changes shows **20 Wh** in the ledger, balances and graphs until resolved.
Before it hears about the other edit it can only show its own. Amount and unit
are considered together: Flow never pairs one device's quantity with the
other device's unit. A delete racing with an edit needs a choice too.

On desktop use **Sync > Conflicts**; on Android open **Files** and select the
version to keep. The choice travels like an edit. If two devices independently
choose different answers, Flow asks again. Undo belongs to the current
session and will not overwrite a newer change it received.

## Optional: keep a desktop peer available after reboot

First pair it and join the files you want it to carry. Then, on that Linux
computer, for a checkout installed at `~/projects/flow`:

```sh
cd ~/projects/flow
systemctl --user enable --now "$PWD/contrib/flow-peer.service"
sudo loginctl enable-linger "$USER"
systemctl --user status flow-peer.service
```

This uses the same device identity and files as the desktop app. It starts
without a graphical login and restarts after failure. Disable it with
`systemctl --user disable --now flow-peer.service`. Other reachable peers can
still exchange edits when this computer is offline.

## Move an existing coordinator file (optional)

Test a separate dataset first. Neither updating Flow nor running the peer
service changes your existing coordinator. Never share independent copies of
a live coordinator file as new datasets. To switch one dataset deliberately:

1. Update **all** its clients. Keep the old coordinator running. Stop editing
   and bring every joined copy online, including phones that have been away.
   Resolve its conflicts and wait until all copies say **Up to date**. Close
   all clients. For a phone, first use **Save As** to export its up-to-date
   SQLite copy for the check below. Do not edit that export.
2. In the Flow checkout, run `python3 -m flowledger.peer_migrate check FILE`
   once for **each** joined copy, using its actual local path (or the phone
   export). Keep the printed receipts. The old service does not have a list of
   every offline client; if a device cannot be accounted for, postpone this.
3. Stop the old coordinator and disable any service that would restart it.
   From its Flow checkout, convert into a **new**, unused local path, using the
   same directory you supplied to the old coordinator's `serve` command:

   ```sh
   python3 -m flowledger.peer_migrate convert \
     --directory "$HOME/.local/share/flow-sync/flow" \
     --output "$HOME/money-peer.sqlite3" --name money \
     --receipt 'DESKTOP_RECEIPT' --receipt 'PHONE_RECEIPT' --all-clients-closed
   ```

   Add a `--receipt` for each remaining device. The converter compares
   revisions and ledger contents, checks the service lock and keeps the old
   database. It refuses a running coordinator, an existing destination, or
   a configured router mirror; keep the coordinator if you rely on that mirror.
4. Open `flow "$HOME/money-peer.sqlite3"` and choose **Share this file**.
   Pair and join on the others. Do not resume editing old copies. Before
   retiring them, compare record count and balances and make one test edit.

The stopped coordinator keeps its settings as `coordinator.retired.json`.
If interrupted during conversion, inspect the output path first. Only if no
peer file was published and no peer edits exist may you rename those settings
back to `coordinator.json` and restart the old service. Once you use the new
file, restarting the old coordinator would create divergent histories.
