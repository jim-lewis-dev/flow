package dev.jimlewis.flow;

import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;
import android.provider.OpenableColumns;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** File identity and SAF writeback. All methods run on the single Python worker.
 * SQLite uses one stable private working file per document URI. The selected
 * document remains the user-visible file: each successful edit writes a complete
 * SQLite backup back to that URI. Dirty working files survive failed writes and
 * process death; they must never be refreshed from an older external document.
 */
final class DocumentFiles {
    interface Engine { JSONObject call(String action, JSONObject args) throws Exception; }
    private static final Set<String> WRITES = new HashSet<>(Arrays.asList(
        "add_flow", "update_flow", "copy_flow", "delete_flow", "save_anchor",
        "save_positions", "undo", "redo"));
    private final Context context;
    private final File root;
    private final SharedPreferences prefs;
    private final Engine engine;
    private String current;
    private String warning = "";
    private boolean ready;

    DocumentFiles(Context context, File root, String legacyDataset, Engine engine) {
        this.context = context.getApplicationContext();
        this.root = root;
        this.engine = engine;
        prefs = context.getSharedPreferences("flow-files", Context.MODE_PRIVATE);
        current = prefs.getString("current", legacyDataset);
    }

    private JSONObject records() throws Exception {
        return new JSONObject(prefs.getString("documents", "{}"));
    }
    private JSONObject document(String key) throws Exception {
        JSONObject record = records().optJSONObject(key);
        if (record == null) throw new IOException("That file is no longer in the file list. Open it again.");
        return record;
    }
    private void remember(String key, JSONObject record) throws Exception {
        JSONObject all = records();
        all.put(key, record);
        if (!prefs.edit().putString("documents", all.toString()).commit())
            throw new IOException("Could not remember the file. Free some phone storage and try again.");
    }
    private static boolean external(String key) { return key != null && key.startsWith("document:"); }
    private void selected(String key) throws Exception {
        current = key;
        if (!prefs.edit().putString("current", key).commit())
            throw new IOException("Could not remember the selected file.");
    }
    private JSONObject checked(String action, JSONObject args) throws Exception {
        JSONObject result = engine.call(action, args);
        if (!result.getBoolean("ok")) throw new IOException(result.optString("error", "The file could not be opened."));
        return result;
    }
    private JSONObject state() throws Exception { return checked("init", new JSONObject()); }

    JSONObject request(String action, JSONObject args) throws Exception {
        if (!ready) initialize();
        if ("init".equals(action)) return decorate(state());
        if ("retry_save".equals(action)) {
            flush();
            return decorate(state());
        }
        if ("open_document".equals(action)) return openRemembered(args.getString("key"));
        if ("open_dataset".equals(action) || "new_dataset".equals(action) || "camp".equals(action) || "sync_join".equals(action)) {
            flush();
            JSONObject response = engine.call(action, args);
            if (response.getBoolean("ok")) {
                selected(response.getJSONObject("result").getJSONObject("state").getString("dataset"));
                warning = "";
            }
            return decorate(response);
        }
        boolean write = WRITES.contains(action) && external(current);
        boolean alreadyDirty = false;
        if (write) {
            JSONObject record = document(current);
            alreadyDirty = record.optBoolean("dirty");
            record.put("dirty", true);
            remember(current, record); // Before the SQLite commit, for crash recovery.
        }
        JSONObject response = engine.call(action, args);
        if (write) {
            if (response.getBoolean("ok")) {
                try { flush(); } catch (Exception error) { warning = saveWarning(error); }
            } else if (!alreadyDirty) {
                JSONObject record = document(current);
                record.put("dirty", false);
                remember(current, record);
            }
        }
        return decorate(response);
    }

    private void initialize() throws Exception {
        if (external(current)) {
            JSONObject record = document(current);
            try { load(current, record); }
            catch (Exception error) {
                checked("open_document", cacheArgs(current, record));
                warning = "Could not reopen " + record.getString("name") + ". Showing the phone working file. " + error.getMessage();
            }
        } else {
            JSONObject result = engine.call("init", new JSONObject().put("dataset", current));
            if (!result.getBoolean("ok")) {
                String[] names = root.list((dir, name) -> !name.startsWith(".") && name.endsWith(".sqlite3"));
                if (names == null || names.length == 0) throw new IOException(result.getString("error"));
                Arrays.sort(names);
                checked("open_dataset", new JSONObject().put("name", names[0]));
                warning = "The previous file is unavailable. Opened " + names[0] + ".";
                selected(names[0]);
            }
        }
        ready = true;
    }

    private JSONObject cacheArgs(String key, JSONObject record) throws Exception {
        return new JSONObject().put("key", key.substring("document:".length())).put("name", record.getString("name"));
    }

    private JSONObject load(String key, JSONObject record) throws Exception {
        if (record.optBoolean("dirty")) {
            JSONObject response = checked("open_document", cacheArgs(key, record));
            selected(key);
            try { flush(); } catch (Exception error) { warning = saveWarning(error); }
            return response;
        }
        Uri uri = Uri.parse(record.getString("uri"));
        JSONObject description = describe(uri);
        record.put("name", description.getString("name")).put("location", description.getString("location"));
        File incoming = new File(root, ".incoming");
        try {
            try (InputStream input = context.getContentResolver().openInputStream(uri);
                 OutputStream output = new FileOutputStream(incoming)) { copy(input, output); }
            String hash = digest(new FileInputStream(incoming));
            JSONObject response = checked("install_document", cacheArgs(key, record));
            record.put("sha256", hash).put("dirty", false);
            record.remove("pending_sha256");
            remember(key, record);
            selected(key);
            warning = "";
            return response;
        } finally { incoming.delete(); }
    }

    private JSONObject openRemembered(String key) throws Exception {
        if (!external(key)) throw new IOException("Choose a file from Files.");
        flush();
        return decorate(load(key, document(key)));
    }

    JSONObject open(Uri uri) throws Exception {
        if (ready) flush();
        String key = "document:" + digest(new java.io.ByteArrayInputStream(uri.toString().getBytes(StandardCharsets.UTF_8)));
        JSONObject record = records().optJSONObject(key);
        if (record == null) record = describe(uri);
        JSONObject response = load(key, record);
        ready = true;
        return decorate(response);
    }

    JSONObject snapshot() throws Exception {
        if (!ready) initialize();
        return checked("prepare_export", new JSONObject()).getJSONObject("result");
    }

    JSONObject saveAs(Uri uri, String snapshot) throws Exception {
        // Save As is explicit. It can also rescue edits when the old location
        // is unavailable. It does not change or delete the previous source file.
        JSONObject old = external(current) ? document(current) : null;
        String oldKey = current;
        try (InputStream input = new FileInputStream(snapshot);
             OutputStream output = context.getContentResolver().openOutputStream(uri, "wt")) { copy(input, output); }
        if (!digest(new FileInputStream(snapshot)).equals(digest(context.getContentResolver().openInputStream(uri))))
            throw new IOException("The saved file could not be verified. Your working file is unchanged.");
        String key = "document:" + digest(new java.io.ByteArrayInputStream(uri.toString().getBytes(StandardCharsets.UTF_8)));
        JSONObject response = load(key, describe(uri));
        if (old != null && !oldKey.equals(key)) {
            old.put("dirty", false);
            old.remove("pending_sha256");
            remember(oldKey, old);
        }
        return decorate(response);
    }

    private void flush() throws Exception {
        if (!external(current)) return;
        JSONObject record = document(current);
        if (!record.optBoolean("dirty")) return;
        Uri uri = Uri.parse(record.getString("uri"));
        // Manual transfer may replace the external file while Flow is open.
        // Refuse to overwrite that file with an older working copy.
        String externalHash = digest(context.getContentResolver().openInputStream(uri));
        if (!record.getString("sha256").equals(externalHash) &&
            !record.optString("pending_sha256").equals(externalHash))
            throw new IOException("The selected file changed outside Flow. Use Save As to keep these phone edits separately before opening the other file.");
        JSONObject snapshot = checked("prepare_export", new JSONObject()).getJSONObject("result");
        File file = new File(snapshot.getString("path"));
        try {
            String hash = digest(new FileInputStream(file));
            // A completed write may outlive this process before preferences
            // commit. Only these exact bytes (or the previous saved bytes) can
            // be retried automatically; an unknown partial write stays local.
            record.put("pending_sha256", hash);
            remember(current, record);
            try (InputStream input = new FileInputStream(file);
                 OutputStream output = context.getContentResolver().openOutputStream(uri, "wt")) { copy(input, output); }
            if (!hash.equals(digest(context.getContentResolver().openInputStream(uri))))
                throw new IOException("The file could not be verified after saving.");
            record.put("sha256", hash).put("dirty", false);
            record.remove("pending_sha256");
            remember(current, record);
            warning = "";
        } finally { file.delete(); }
    }

    private String saveWarning(Exception error) {
        return "Changes are saved on this phone, but not to the selected file. " + error.getMessage();
    }

    private JSONObject describe(Uri uri) throws Exception {
        String name = null;
        try (Cursor cursor = context.getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) name = cursor.getString(0);
        }
        if (name == null || name.isEmpty()) throw new IOException("The selected file has no filename.");
        String location = uri.getAuthority();
        try {
            String id = DocumentsContract.getDocumentId(uri);
            if (id.startsWith("primary:")) location = "Internal storage/" + id.substring(8);
            else if (id.contains(":")) location = id.replace(':', '/');
            else {
                android.content.pm.ProviderInfo provider = context.getPackageManager().resolveContentProvider(uri.getAuthority(), 0);
                if (provider != null) location = provider.loadLabel(context.getPackageManager()).toString();
            }
        } catch (IllegalArgumentException ignored) { }
        return new JSONObject().put("uri", uri.toString()).put("name", name).put("location", location);
    }

    private JSONObject decorate(JSONObject response) throws Exception {
        JSONObject result = response.optJSONObject("result");
        JSONObject state = result == null ? null : result.optJSONObject("state");
        if (state == null) return response;
        JSONArray files = new JSONArray();
        JSONArray names = state.getJSONArray("datasets");
        for (int i = 0; i < names.length(); i++) {
            String name = names.getString(i);
            files.put(new JSONObject().put("key", name).put("name", name).put("location", "On this phone"));
        }
        JSONObject all = records();
        JSONArray keys = all.names();
        if (keys != null) for (int i = 0; i < keys.length(); i++) {
            String key = keys.getString(i);
            JSONObject record = all.getJSONObject(key);
            files.put(new JSONObject().put("key", key).put("name", record.getString("name")).put("location", record.getString("location")));
        }
        state.put("files", files).put("storage_warning", warning);
        state.put("file_location", external(current) ? document(current).getString("location") : "On this phone · Flow files");
        state.put("file_pending", external(current) && document(current).optBoolean("dirty"));
        return response;
    }

    static void copy(InputStream input, OutputStream output) throws Exception {
        if (input == null || output == null) throw new IOException("The file could not be opened.");
        byte[] buffer = new byte[65536];
        int count;
        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
        output.flush();
    }
    private static String digest(InputStream input) throws Exception {
        if (input == null) throw new IOException("The file could not be opened.");
        try (InputStream stream = input) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[65536];
            int count;
            while ((count = stream.read(buffer)) != -1) digest.update(buffer, 0, count);
            StringBuilder hex = new StringBuilder();
            for (byte value : digest.digest()) hex.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            return hex.toString();
        }
    }
}
