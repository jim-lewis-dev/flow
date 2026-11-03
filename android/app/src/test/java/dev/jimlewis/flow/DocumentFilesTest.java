package dev.jimlewis.flow;

import android.app.Application;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.ProviderInfo;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowContentResolver;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.nio.file.StandardCopyOption;
import java.util.HashMap;
import java.util.Map;
import static org.junit.Assert.*;

/** Tests the real Android file coordinator, not the native picker UI or SQLite.
 * MobileStore's SQLite validation/migration is covered by tests/test_mobile.py.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, application = Application.class)
public class DocumentFilesTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private Context context;
    private File root;
    private TestDocuments provider;
    private Engine engine;
    private DocumentFiles files;

    @Before public void setUp() throws Exception {
        context = RuntimeEnvironment.getApplication();
        context.getSharedPreferences("flow-files", Context.MODE_PRIVATE).edit().clear().commit();
        root = temp.newFolder("datasets");
        write(new File(root, "flow.sqlite3").toPath(), "phone-only");
        provider = new TestDocuments();
        ProviderInfo info = new ProviderInfo();
        info.authority = "flow.test.documents";
        provider.attachInfo(context, info);
        ShadowContentResolver.registerProviderInternal(info.authority, provider);
        restart();
    }

    private static void write(Path path, String text) throws IOException {
        Files.write(path, text.getBytes(StandardCharsets.UTF_8));
    }
    private static String read(Path path) throws IOException {
        return new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
    }
    private void restart() {
        engine = new Engine(root);
        files = new DocumentFiles(context, root, "flow.sqlite3", engine);
    }
    private Uri document(String path, String text) throws Exception {
        Uri uri = Uri.parse("content://flow.test.documents/document/" + Uri.encode("primary:" + path));
        File file = temp.newFile();
        write(file.toPath(), text);
        provider.documents.put(uri, file);
        return uri;
    }
    private String content(Uri uri) throws Exception {
        return read(provider.documents.get(uri).toPath());
    }
    private static JSONObject state(JSONObject response) throws Exception {
        assertTrue(response.toString(), response.getBoolean("ok"));
        return response.getJSONObject("result").getJSONObject("state");
    }
    private JSONObject edit(String text) throws Exception {
        return state(files.request("update_flow", new JSONObject().put("text", text)));
    }

    @Test public void openingSameFileDoesNotDuplicateAndReopensLastFile() throws Exception {
        Uri uri = document("Documents/flow/money.sqlite3", "desktop-original");
        JSONObject first = state(files.open(uri));
        String key = first.getString("dataset");
        assertEquals("money.sqlite3", first.getString("dataset_name"));
        assertEquals("Internal storage/Documents/flow/money.sqlite3", first.getString("file_location"));
        assertEquals(key, state(files.open(uri)).getString("dataset"));
        assertEquals(2, state(files.request("init", new JSONObject())).getJSONArray("files").length());
        assertEquals(1, new File(root, ".documents").list().length);
        assertArrayEquals(new String[]{"flow.sqlite3"}, root.list((dir, name) -> name.endsWith(".sqlite3")));

        write(provider.documents.get(uri).toPath(), "new-desktop-transfer");
        restart();
        JSONObject reopened = state(files.request("init", new JSONObject()));
        assertEquals(key, reopened.getString("dataset"));
        assertEquals("new-desktop-transfer", reopened.getString("text"));
        assertEquals("phone-only", read(new File(root, "flow.sqlite3").toPath()));
    }

    @Test public void identicalFilenamesInDifferentFoldersStaySeparate() throws Exception {
        Uri first = document("Documents/one/money.sqlite3", "first");
        Uri second = document("Documents/two/money.sqlite3", "second");
        String firstKey = state(files.open(first)).getString("dataset");
        edit("first-edited");
        String secondKey = state(files.open(second)).getString("dataset");
        assertNotEquals(firstKey, secondKey);
        assertEquals("second", engine.text());
        edit("second-edited");
        JSONObject returned = state(files.request("open_document", new JSONObject().put("key", firstKey)));
        assertEquals("first-edited", returned.getString("text"));
        assertEquals("first-edited", content(first));
        assertEquals("second-edited", content(second));
        assertEquals(2, new File(root, ".documents").list().length);
    }

    @Test public void editsAndUndoWriteBackToTheSelectedDocument() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "before");
        files.open(uri);
        JSONObject edited = edit("after");
        assertEquals("after", content(uri));
        assertFalse(edited.getBoolean("file_pending"));
        assertEquals("", edited.getString("storage_warning"));
        files.request("undo", new JSONObject());
        assertEquals("before", content(uri));
        files.request("redo", new JSONObject());
        assertEquals("after", content(uri));
    }

    @Test public void failedSaveKeepsEditsAcrossRestartAndRetry() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "before");
        files.open(uri);
        provider.failWrites = true;
        JSONObject pending = edit("phone-edit");
        assertTrue(pending.getBoolean("file_pending"));
        assertFalse(pending.getString("storage_warning").isEmpty());
        assertEquals("before", content(uri));
        restart();
        JSONObject reopened = state(files.request("init", new JSONObject()));
        assertEquals("phone-edit", reopened.getString("text"));
        assertTrue(reopened.getBoolean("file_pending"));
        provider.failWrites = false;
        JSONObject saved = state(files.request("retry_save", new JSONObject()));
        assertEquals("phone-edit", content(uri));
        assertFalse(saved.getBoolean("file_pending"));
    }

    @Test public void externalChangesAreProtectedAndSaveAsRescuesPhoneEdits() throws Exception {
        Uri original = document("Documents/money.sqlite3", "before");
        files.open(original);
        provider.failWrites = true;
        edit("phone-edit");
        provider.failWrites = false;
        write(provider.documents.get(original).toPath(), "new-desktop-edit");
        IOException conflict = assertThrows(IOException.class,
            () -> files.request("retry_save", new JSONObject()));
        assertTrue(conflict.getMessage(), conflict.getMessage().contains("changed outside Flow"));
        assertEquals("new-desktop-edit", content(original));
        assertEquals("phone-edit", engine.text());

        Uri rescued = document("Documents/recovered.sqlite3", "");
        String snapshot = files.snapshot().getString("path");
        JSONObject saved = state(files.saveAs(rescued, snapshot));
        assertEquals("recovered.sqlite3", saved.getString("dataset_name"));
        assertEquals("phone-edit", content(rescued));
        assertEquals("new-desktop-edit", content(original));
        assertFalse(saved.getBoolean("file_pending"));
        restart();
        assertEquals("recovered.sqlite3", state(files.request("init", new JSONObject())).getString("dataset_name"));
        assertEquals("phone-edit", engine.text());
    }

    @Test public void partialWriteNeverDiscardsTheWorkingFile() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "before");
        files.open(uri);
        provider.partialFailure = true;
        assertTrue(edit("phone-edit").getBoolean("file_pending"));
        provider.partialFailure = false;
        assertThrows(IOException.class, () -> files.request("retry_save", new JSONObject()));
        restart();
        JSONObject reopened = state(files.request("init", new JSONObject()));
        assertEquals("phone-edit", reopened.getString("text"));
        assertTrue(reopened.getBoolean("file_pending"));
        assertEquals("partial", content(uri));
    }

    @Test public void completedWriteBeforeProcessDeathCanBeRecovered() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "before");
        files.open(uri);
        provider.failWrites = true;
        edit("phone-edit");
        // Simulate write success followed by process death before clearing dirty.
        write(provider.documents.get(uri).toPath(), "phone-edit");
        provider.failWrites = false;
        restart();
        JSONObject reopened = state(files.request("init", new JSONObject()));
        assertEquals("phone-edit", reopened.getString("text"));
        assertFalse(reopened.getBoolean("file_pending"));
        assertEquals("phone-edit", content(uri));
    }

    @Test public void unavailableSourceReopensWorkingFileWithVisibleWarning() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "before");
        String key = state(files.open(uri)).getString("dataset");
        provider.failReads = true;
        restart();
        JSONObject reopened = state(files.request("init", new JSONObject()));
        assertEquals(key, reopened.getString("dataset"));
        assertEquals("before", reopened.getString("text"));
        assertFalse(reopened.getString("storage_warning").isEmpty());
    }

    @Test public void joinedLocalFileReopensWithoutWritingToPreviousDocument() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "manual-file");
        files.open(uri);
        JSONObject joined = state(files.request("sync_join", new JSONObject().put("name", "shared.sqlite3")));
        assertEquals("shared.sqlite3", joined.getString("dataset"));
        assertEquals("On this phone · Flow files", joined.getString("file_location"));
        edit("queued-local-edit");
        assertEquals("manual-file", content(uri));
        restart();
        JSONObject reopened = state(files.request("init", new JSONObject()));
        assertEquals("shared.sqlite3", reopened.getString("dataset"));
        assertEquals("queued-local-edit", reopened.getString("text"));
    }

    @Test public void rejectedEditDoesNotMarkTheFileDirty() throws Exception {
        Uri uri = document("Documents/money.sqlite3", "before");
        files.open(uri);
        assertFalse(files.request("update_flow", new JSONObject().put("invalid", true)).getBoolean("ok"));
        assertFalse(state(files.request("init", new JSONObject())).getBoolean("file_pending"));
        assertEquals("before", content(uri));
    }

    public static final class TestDocuments extends ContentProvider {
        final Map<Uri, File> documents = new HashMap<>();
        boolean failReads, failWrites, partialFailure;
        @Override public boolean onCreate() { return true; }
        @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) {
            MatrixCursor cursor = new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME});
            String path = uri.getLastPathSegment();
            cursor.addRow(new Object[]{path.substring(path.lastIndexOf('/') + 1)});
            return cursor;
        }
        @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
            File file = documents.get(uri);
            if (file == null || (mode.equals("r") && failReads)) throw new FileNotFoundException("Unavailable document");
            if (mode.contains("w")) {
                if (partialFailure) {
                    try { write(file.toPath(), "partial"); }
                    catch (IOException error) { throw new FileNotFoundException(error.getMessage()); }
                    throw new FileNotFoundException("Interrupted write");
                }
                if (failWrites) throw new FileNotFoundException("Storage is unavailable");
            }
            return ParcelFileDescriptor.open(file, ParcelFileDescriptor.parseMode(mode));
        }
        @Override public String getType(Uri uri) { return "application/octet-stream"; }
        @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
        @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
        @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    }

    /** Minimal persistent engine double: real file IO, no fabricated provider results. */
    private static final class Engine implements DocumentFiles.Engine {
        final File root;
        File current;
        String key, name, previous, next;
        Engine(File root) { this.root = root; }
        String text() throws IOException { return read(current.toPath()); }
        private void select(File file, String identity, String filename) throws IOException {
            if (!file.isFile()) throw new IOException("Missing working file");
            current = file; key = identity; name = filename;
        }
        @Override public JSONObject call(String action, JSONObject args) throws Exception {
            if ("init".equals(action) && current == null) {
                name = args.optString("dataset", "flow.sqlite3");
                select(new File(root, name), name, name);
            } else if ("sync_join".equals(action)) {
                name = args.getString("name");
                File target = new File(root, name);
                write(target.toPath(), "shared-seed");
                select(target, name, name);
            } else if ("install_document".equals(action) || "open_document".equals(action)) {
                File cache = new File(root, ".documents/" + args.getString("key") + ".sqlite3");
                if ("install_document".equals(action)) {
                    cache.getParentFile().mkdirs();
                    Files.copy(new File(root, ".incoming").toPath(), cache.toPath(), StandardCopyOption.REPLACE_EXISTING);
                }
                select(cache, "document:" + args.getString("key"), args.getString("name"));
            } else if ("prepare_export".equals(action)) {
                File snapshot = new File(root, ".export.sqlite3");
                Files.copy(current.toPath(), snapshot.toPath(), StandardCopyOption.REPLACE_EXISTING);
                return new JSONObject().put("ok", true).put("result", new JSONObject().put("path", snapshot.getPath()).put("name", name));
            } else if ("update_flow".equals(action)) {
                if (args.optBoolean("invalid")) return new JSONObject().put("ok", false).put("error", "Invalid field");
                previous = text(); next = args.getString("text");
                write(current.toPath(), next);
            } else if ("undo".equals(action)) write(current.toPath(), previous);
            else if ("redo".equals(action)) write(current.toPath(), next);
            JSONObject state = new JSONObject().put("dataset", key).put("dataset_name", name).put("text", text())
                .put("datasets", new JSONArray(root.list((dir, filename) -> !filename.startsWith(".") && filename.endsWith(".sqlite3"))));
            return new JSONObject().put("ok", true).put("result", new JSONObject().put("state", state));
        }
    }
}
