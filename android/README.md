# flow for Android

A phone interface for Flow's multi-resource ledger. Quantities move between
accounts, in any unit. The Android app uses the **same Python data engine and
SQLite format** as desktop Flow.

This is an Android preview, with a touch interface for flows, account balances,
graphs, an animated account map, and database files. It runs offline. Optional
local-network synchronization keeps a phone file and desktop copies current
through a coordinator on one of your computers.

Start with the camp example to check the controls on your phone. This build has
not been tested on a physical Galaxy S24; see the checks below for the testing
completed before delivery.

## Install on a Galaxy S24

Download `flow-android-preview.apk` on your phone, open it in **My Files**, and tap
**Install**. If asked, allow **Install unknown apps** for My Files. Samsung's
**Auto Blocker** may need to be turned off for the installation; restore your
usual security settings afterward. No rooting or developer mode is required
for the ordinary APK installation path.

If the earlier preview is installed, install this APK over it; there is no need
to uninstall it. The package and signing key are unchanged.

The app appears as **flow**. Open **Files → Try the camp example** to explore
battery energy in Wh and water in L without using your own records.

## Your file, on your phone

**Files** shows the current filename and location. Flow remembers the last file
and reopens it next time.

- **Create:** enter a name such as `calories` to create `calories.sqlite3` in
  Flow's private phone folder. It stays on your phone unless you transfer it.
  An existing name is rejected, never silently numbered or overwritten.
- **Open file:** choose a Flow file anywhere Android's picker provides writable
  access. Valid edits save back to that selected file. Opening it again uses
  the same file; it does not create “flow 2.”
- **Your files:** switch between phone files and previously opened files. The
  location distinguishes files with identical names.
- **Save As:** choose a new file and location, then continue working there.
  The previous file remains unchanged. Use this to put a phone-only file in
  Downloads or Documents for transfer.

For manual transfer, close Flow on the device you are leaving, copy the current
`.sqlite3` file, and open it on the other device. Work on one device at a time;
edits made independently on both devices do not merge. A folder such as
**Documents/flow** is a convenient place for transferred files. The Open picker
remembers the last selected location.

Android's document picker supplies a document URI rather than a normal SQLite
path. Flow therefore keeps one private working file per chosen document and
writes a consistent SQLite snapshot back after each saved change. This is an
internal working file, not another dataset in the file list. If saving fails,
a persistent message appears and the phone edits remain recoverable. Retry
after restoring access, or use Save As. If the original changed outside Flow,
it is not silently overwritten.

Existing phone files from earlier previews are preserved. Files kept only in
Flow's private folder need Save As before uninstalling or clearing app data.
Installing an update over the existing app preserves them.

Files use schema 8, including saved account positions. Schema 5–7 files are
upgraded when opened; older files need current desktop Flow first. Records,
starting balances, audit history, units, and map positions travel together.

## Synchronize with a computer

Set up one coordinator using the desktop
[synchronization guide](../docs/synchronization.md).
On the phone, open **Files → Synchronization**, enter a local filename, paste
the coordinator's connection text, and tap **Join shared file**. A matching local
file connects in place; a new name creates a local copy. A different existing
file is never overwritten.

Valid edits save immediately on the phone. While Flow is in the foreground,
it checks for changes about every two seconds and immediately after a save.
Leaving the app makes a best-effort final sync. Offline edits remain in the
same SQLite file and synchronize when the connection returns. The status shows
pending changes, an unavailable coordinator, or **Up to date**. There is no
Android background synchronization service.

If the coordinator moves to another network address, paste its revised
connection text and choose **Update connection** for the current synchronized
file. This preserves pending edits and refuses a different shared dataset.

Use this local joined file for sync. **Open file** and **Save As** retain their
manual document-file behavior; the app does not synchronize a network document
by repeatedly replacing its entire SQLite file.

If another device changes the same field before your edit arrives, choose
**Keep this device's edit** or **Use received change**. The choice applies to
that saved action; later queued actions remain available. Undo reverses this
phone's session actions and stops if it would overwrite a newer received
change. Starting balances and saved account-map positions synchronize too;
unfinished forms and filters stay on their device.

## Use it

- **Phone layout:** compact rows and spacing; pinch regular pages to zoom.
  Graphs and Experimental handle pinches within their own plots. Android's
  bottom navigation bar hides automatically; swipe from the bottom edge to
  reveal it temporarily. The layout stays clear of the status bar and keyboard.
- **Flows:** search and combine From, To, Units, Status, Categories, and Tags
  filters. Tap a record to edit it. Valid edits save when you leave a field.
- **Add Flow:** fill in Date, From, To, Amount, and Units; the hint tracks what
  is missing. Amounts accept `+`, `-`, `*`, `/`, parentheses, powers such as `2^3`, and an
  optional leading `=`. Zero,
  negative quantities, and matching endpoints work as on desktop.
- **Dates:** type a year-first date or use the calendar beside it. Missing
  month/day default to 1; missing minutes/seconds default to 00. For example,
  `2026-11 9` becomes `2026-11-01 09:00:00`. Leading zeros are optional.
  Formatting happens when you leave or commit the field. Date-only entries
  remain date-only; choosing a calendar day preserves an entered time.
- **Clear Flow:** reset the form; disabled when it is already clear. Add Flow
  becomes available when the required fields are filled. An unadded draft survives closing the form
  and switching tabs during the session.
- **Duplicate / Delete:** available inside an existing record. Duplication
  preserves every field, including date, and assigns a fresh flow ID.
- **Undo / Redo:** the two arrows at the top reverse saved record and starting
  balance changes, plus account moves in Experimental, during the running session.
  Filters and drafts are not in
  the phone's undo stack yet.
- **Accounts:** each account/unit pair has its own balance. Tap to edit the
  starting date and balance; blank means unknown. Set **As of** to include
  future pending or planned records.
- **Graphs:** select account/unit pairs and a status basis. Different units
  have separate plots. Touch for exact values; pinch to zoom, drag to pan,
  and use Fit to restore the full range.
- **Files:** create, open, switch, save a file in another location, or join a
  synchronized file.
- **Experimental:** play transfers between account nodes. It uses the current
  Flows filters, with its own unit, date range, and balance basis.

Complete, Pending, Planned, and Canceled retain their desktop meanings. Canceled
records affect no balances. Unknown starting balances remain unknown. Quantities
stay exact decimal strings across the phone interface; only graph coordinates
use floating point.

## Experimental

Choose **Files → Try the camp example**, then **Experimental**. Switch between
**Wh** for the battery and equipment, and **L** for water use.

- Play or pause; step one timestamp forward or back; jump to the start or end.
- Set speed from 0 to 5. Zero pauses playback.
- Drag an account to move it. Drag empty space to pan; pinch to zoom; tap **Fit**
  to bring the map back into view.
- Tap an account for incoming and outgoing totals and its balance at that time.
  Tap a moving flow for its record details.

Account positions save in the database and travel with an exported file. Moving
nodes changes the layout, not the flows. Canceled records are excluded; negative
quantities travel in the reverse direction. Different units remain separate.

## What is in this preview

This is a focused phone adaptation. Desktop batch selection, CSV/JSON import,
combined graph lines, and undo for view settings are not included. There is no
iPhone build, hosted cloud service, or Play Store release.

## Checks for this build

- Python tests cover records, exact quantities, balances, graphs, map
  positions, partial dates, file switching, and validated SQLite round trips.
- Native Java tests exercise file coordination with a mock Android document
  provider: autosave, reopening, duplicate basenames, unavailable files,
  interrupted writes, conflict protection, and Save As recovery.
- Browser checks at 360 and 412 pixels wide cover separate phone files,
  record editing, switching, duplicate-name rejection, calendar controls,
  date/time ranges, and reachable navigation. The browser uses the real Python
  adapter; native document pickers require Android.
- The signed release builds, matches the previous preview's signing certificate,
  and passes APK and native-library 16 KB alignment checks.

This update has not been run on a physical Galaxy S24. Mock-provider tests
exercise native file coordination, but do not operate Samsung's actual picker.

## Build and check

Open `android/` in Android Studio, or use JDK 17, Android SDK platform 35 and
build tools 35.0.0:

```bash
cd android
./gradlew assembleDebug
```

The Gradle wrapper downloads Gradle 8.13. The build uses Android Gradle Plugin
8.9.2 and Chaquopy 17.2.0 to bundle Python 3.12 for ARM64 phones and x86-64
emulators. No Python installation is needed on the phone. No Tkinter or
Matplotlib is bundled: the touch UI is HTML/CSS/JavaScript in an offline Android
WebView, with a small Java file-picker bridge.

`flowledger/` contains the shared desktop engine. `mobile/bridge.py` adapts its
APIs. All database requests run on one process-wide worker to preserve
transaction order and SQLite thread ownership. Sync network requests use a
separate worker so connection delays do not block local edits.

The APK uses Android's ordinary Internet permission for the configured HTTPS
coordinator; the connection text pins its certificate. It requests no broad
storage permission. The Android document picker grants access only to files
you choose. Sync credentials and pending changes are stored inside the local
SQLite file.

Run the portable adapter tests:

```bash
python3 -B -m unittest discover -s tests -v
```

Run the native file-coordination tests with `./gradlew testDebugUnitTest` from
`android/`. JUnit and Robolectric are test dependencies only; they are not
bundled in the APK.

The debug build also packages these same adapter tests for Android's Python
runtime. With the debug APK installed on a test device:

```bash
adb shell am instrument -w dev.jimlewis.flow.preview/dev.jimlewis.flow.EngineChecks
```

The result must report `success=true` with no failures or errors. The instrumentation runner
and test modules are excluded from the release APK.

For local browser development only:

```bash
python3 -m mobile.preview_server --data /tmp/flow-phone-test
```

Then open `http://127.0.0.1:8768/index.html`. The development harness does not
implement Android's native Open/Save As pickers; those require Android.

Release signing reads `FLOW_ANDROID_KEYSTORE` and
`FLOW_ANDROID_KEY_PASSWORD` from the build environment, with alias
`flow-preview`. Keep signing material private and outside the source tree.
An unrelated debug key cannot update an installed signed preview in place.

## License

[MIT](../LICENSE) © 2026 Jim Lewis. Python and Chaquopy retain their own bundled
license notices.
