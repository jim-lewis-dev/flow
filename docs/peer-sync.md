# Share a Flow file

Each device works on a local SQLite file. Pair devices once, then pick the
files to share. Either device can create the original. No computer is in
charge. Files stay local until you choose **Share this file**.

## Pair, share, join

1. Connect both devices to the same LAN. Open a test file on the first device
   (for example, `flow peer-test` on Linux).
2. Open **Sync > Devices > Pair device** on desktop or **Files > Pair
   device** on the phone, on both devices. Select the other device on one
   screen, then choose **Pair** on the receiving device.
   Selecting the device sends the request; only the receiver confirms.
   Nothing needs to be typed. Keep both pairing screens open until paired.
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
the old name alone cannot grant access. Paired devices can read and join every
file the other device explicitly shares. Keep unrelated files local. Forgetting
a device removes its saved connection and its status errors immediately. It keeps local files and cannot recall copies on other devices.
The other device may still remember the old pairing; it can accept a fresh
request with a new confirmation.

Desktop joins use `~/.local/share/flowledger/shared/<name>.sqlite3`, or the
equivalent location under `$XDG_DATA_HOME`. Choose a readable, unique filename;
existing files are never overwritten or silently numbered. If the shared name
is not a valid filename or is already taken here, choose a different shared
name on the device offering the file before joining.

Flows, account starting balances, tags and saved node positions travel with
their changes. The undo stack, screen selections and audit log stay local.
Each shared file retains its edit history so a device can catch up later.
On Linux, device keys and the list of files you share live separately under
`~/.config/flow/peers/` (or `$XDG_CONFIG_HOME/flow/peers/`). Those settings
belong to that installation; do not copy them to another device.

Devices exchange changes while Flow is open and the phone app is in the
foreground. If A meets B today and B meets C tomorrow, C can learn A's changes
even if A is off. If **no pair ever
overlaps**, nothing travels. Changes wait on disk. An idle network drive is
not a peer. Do not open the same live SQLite file on multiple devices.

If a name does not appear, check that both devices are in nearby pairing and
on the same home network. **Find by address** can use the other device's
Wi-Fi or Ethernet address, or a hostname your network resolves. It uses
the same pairing request and acceptance; the port is optional. A timeout means Flow
could not complete the connection. Guest Wi-Fi isolation, a firewall, or a
VPN that blocks LAN access can prevent this. On Linux, `ip -br -4 addr`
shows which address belongs to each interface.

The peer listener uses TCP 47621; automatic discovery uses UDP 47622.
Desktop peers announce on Wi-Fi and Ethernet using multicast and local subnet
broadcasts. Discovery keeps listening if an announcement method fails and
retries when the network changes. Errors appear on the Devices page. Devices
already paired are listed under Paired rather than offered for pairing again.
Each computer with an active firewall needs to allow these ports on its LAN.
No router port forwarding is needed. If you use UFW, scope any rules to your
actual LAN. For example, if your LAN is `192.168.0.0/24` and your firewall is
blocking Flow:

```sh
sudo ufw allow from 192.168.0.0/24 to any port 47621 proto tcp
sudo ufw allow from 192.168.0.0/24 to any port 47622 proto udp
```

Keep Flow current on every device. Device pairings survive updates. Local
files can be shared directly; sharing gives them a new dataset identity.

Linux uses OpenSSL to create a device identity once; the phone uses Android.
The initial snapshot is limited to 56 MiB of serialized records. After joining, edits travel in batches of up to
2 MiB. There is no network transfer of the live SQLite file.

## How edits merge

Edits merge automatically by field. A later change to notes keeps an earlier
change to the amount. If disconnected devices both change the amount, the
latest actual edit wins once their changes arrive. Merely saving unchanged
values or forwarding an edit does not make them newer. Amount and units are
one field: Flow always keeps a quantity with its unit.

A delete wins over an older edit. An actual edit made after the delete brings
the record back, including when that edit was made offline before its device
received the deletion. Unchanged values do not bring a record back.

Flow uses the time recorded for each edit, with a deterministic tie-breaker,
so keep device clocks correct. Delivery order does not change the result.
There are no choices to review. Undo belongs to the current session and will
not overwrite a newer change it received.
