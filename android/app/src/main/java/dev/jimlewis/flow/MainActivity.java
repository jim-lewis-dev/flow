package dev.jimlewis.flow;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.DocumentsContract;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import com.chaquo.python.Python;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.util.ArrayDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Offline Android host. Only bundled assets can access the Python bridge. */
public final class MainActivity extends Activity {
    private static final String HOST = "flow.local";
    private static final int OPEN = 1, SAVE_AS = 2;
    // Python's SQLite connection and undo history belong to this process worker,
    // including after Android recreates the Activity.
    private static final ExecutorService worker = Executors.newSingleThreadExecutor();
    private static final ExecutorService peerLife = Executors.newSingleThreadExecutor();
    private static final ExecutorService peerRequests = Executors.newSingleThreadExecutor();
    private static DocumentFiles files;
    private WebView web;
    private String fileRequestId;
    private String exportPath;
    private File dataDirectory;
    private boolean pageReady;
    private boolean keyboardVisible;
    private final ArrayDeque<String> replies = new ArrayDeque<>();

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        if (android.os.Build.VERSION.SDK_INT >= 30) getWindow().setDecorFitsSystemWindows(false);
        dataDirectory = new File(getFilesDir(), "datasets");
        dataDirectory.mkdirs();
        PeerHost.initialize(this);
        if (saved != null) {
            fileRequestId = saved.getString("fileRequestId");
            exportPath = saved.getString("exportPath");
        }
        web = new WebView(this);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.setBackgroundColor(Color.rgb(245, 246, 243));
        web.setSoundEffectsEnabled(false);
        web.setHapticFeedbackEnabled(false);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setBlockNetworkLoads(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);
        web.addJavascriptInterface(new Bridge(), "FlowNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                pageReady = true;
                while (!replies.isEmpty()) view.evaluateJavascript(replies.removeFirst(), null);
            }

            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return true; // No external pages, frames, or redirects enter the privileged view.
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String path = uri.getPath();
                if ("https".equals(uri.getScheme()) && HOST.equals(uri.getHost()) &&
                    ("/index.html".equals(path) || "/app.js".equals(path) ||
                     "/experimental.js".equals(path) || "/style.css".equals(path))) {
                    try {
                        String mime = path.endsWith(".js") ? "application/javascript" :
                                      path.endsWith(".css") ? "text/css" : "text/html";
                        return new WebResourceResponse(mime, "UTF-8", getAssets().open(path.substring(1)));
                    } catch (Exception ignored) { }
                }
                return new WebResourceResponse("text/plain", "UTF-8", 403, "Blocked",
                    java.util.Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
            }
        });
        // Inset the container, not WebView: fixed HTML controls use the WebView
        // viewport and can ignore its native padding. Shrink that viewport so
        // tabs and dialogs stay clear of system bars, cutouts, and the keyboard.
        FrameLayout content = new FrameLayout(this);
        content.setBackgroundColor(Color.rgb(245, 246, 243));
        content.addView(web, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            content.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                boolean visible = insets.isVisible(WindowInsets.Type.ime());
                if (keyboardVisible && !visible) view.post(this::hideNavigationBar);
                keyboardVisible = visible;
                return WindowInsets.CONSUMED;
            });
        }
        // Older Android versions retain decor fitting and adjustResize.
        setContentView(content);
        content.requestApplyInsets();
        web.loadUrl("https://" + HOST + "/index.html");
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::handleBack);
        }
    }

    private DocumentFiles files() {
        if (files == null) {
            String root = dataDirectory.getAbsolutePath();
            files = new DocumentFiles(getApplicationContext(), dataDirectory,
                getPreferences(MODE_PRIVATE).getString("dataset", "flow.sqlite3"),
                (action, args) -> new JSONObject(Python.getInstance().getModule("mobile.bridge")
                    .callAttr("request", root, new JSONObject().put("action", action).put("args", args).toString()).toString()));
        }
        return files;
    }

    private void answer(String id, String response) {
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) return;
            String script = "window.flowResponse(" + JSONObject.quote(id) + "," + response + ")";
            // A document-picker result may arrive while a recreated Activity
            // is still loading its page. Keep that result until the bridge exists.
            if (pageReady) web.evaluateJavascript(script, null);
            else replies.addLast(script);
        });
    }

    private void fail(String id, Exception error) {
        String message = error.getMessage() == null ? "The operation could not be completed." : error.getMessage();
        answer(id, "{\"ok\":false,\"error\":" + JSONObject.quote(message) + "}");
    }

    private void launchPicker(String id, Intent intent, int request, String snapshot) {
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) {
                if (snapshot != null) new File(snapshot).delete();
                return;
            }
            fileRequestId = id;
            exportPath = snapshot;
            try {
                startActivityForResult(intent, request);
            } catch (Exception error) {
                fileRequestId = null;
                exportPath = null;
                if (snapshot != null) new File(snapshot).delete();
                fail(id, error);
            }
        });
    }

    private final class Bridge {
        @JavascriptInterface public void request(String id, String payload) {
            worker.execute(() -> {
                try {
                    JSONObject request = new JSONObject(payload);
                    String action = request.getString("action");
                    if ("peer_pair".equals(action) ||
                        "peer_catalog".equals(action) || "peer_join".equals(action) || "peer_forget".equals(action)) {
                        JSONObject args = request.optJSONObject("args");
                        peerRequests.execute(() -> {
                            try {
                                JSONObject result = new JSONObject(Python.getInstance().getModule("mobile.peers")
                                    .callAttr("network", dataDirectory.getAbsolutePath(), action,
                                        args == null ? "{}" : args.toString()).toString());
                                if ("peer_join".equals(action) && result.getBoolean("ok")) {
                                    // Opening a new local copy uses the normal SAFE file switch:
                                    // failed document writeback preserves the active working file.
                                    worker.execute(() -> {
                                        try { answer(id, files().request("open_dataset", result.getJSONObject("result")).toString()); }
                                        catch (Exception error) { fail(id, error); }
                                    });
                                } else answer(id, result.toString());
                            } catch (Exception error) { fail(id, error); }
                        });
                    } else if ("pick_open".equals(action)) {
                        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*")
                            .addCategory(Intent.CATEGORY_OPENABLE)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                                      Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
                        String previous = getPreferences(MODE_PRIVATE).getString("picker_location", null);
                        if (previous != null) intent.putExtra(DocumentsContract.EXTRA_INITIAL_URI, Uri.parse(previous));
                        launchPicker(id, intent, OPEN, null);
                    } else if ("save_as".equals(action)) {
                        JSONObject result = files().snapshot();
                        // Keep the picker snapshot separate from autosave's
                        // temporary backup, including across Activity recreation.
                        File held = new File(dataDirectory, ".save-as.sqlite3");
                        java.nio.file.Files.move(new File(result.getString("path")).toPath(), held.toPath(),
                            java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                        String snapshot = held.getAbsolutePath();
                        String name = result.getString("name");
                        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                            .addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream")
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                                      Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
                            .putExtra(Intent.EXTRA_TITLE, name);
                        launchPicker(id, intent, SAVE_AS, snapshot);
                    } else {
                        // The page cannot choose private cache paths or bypass the picker.
                        if ("install_document".equals(action) || "prepare_export".equals(action))
                            throw new IllegalArgumentException("Use the Files screen.");
                        JSONObject args = request.optJSONObject("args");
                        answer(id, files().request(action, args == null ? new JSONObject() : args).toString());
                    }
                } catch (Exception error) { fail(id, error); }
            });
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != OPEN && request != SAVE_AS) return;
        String id = fileRequestId == null ? "file" : fileRequestId;
        String snapshot = exportPath;
        fileRequestId = null;
        exportPath = null;
        if (result != RESULT_OK || data == null || data.getData() == null) {
            if (snapshot != null) new File(snapshot).delete();
            answer(id, "{\"ok\":true,\"result\":{\"canceled\":true}}");
            return;
        }
        Uri uri = data.getData();
        worker.execute(() -> {
            try {
                int flags = data.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                if (flags != (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION))
                    throw new java.io.IOException("Choose a file that Flow can read and write.");
                getContentResolver().takePersistableUriPermission(uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                if (request == OPEN) {
                    answer(id, files().open(uri).toString());
                } else {
                    if (snapshot == null) throw new java.io.IOException("Please choose Save As again.");
                    answer(id, files().saveAs(uri, snapshot).toString());
                }
                getPreferences(MODE_PRIVATE).edit().putString("picker_location", uri.toString()).apply();
            } catch (Exception error) { fail(id, error); }
            finally { if (snapshot != null) new File(snapshot).delete(); }
        });
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString("fileRequestId", fileRequestId);
        out.putString("exportPath", exportPath);
    }

    private void handleBack() {
        if (!pageReady) { finish(); return; }
        web.evaluateJavascript("window.flowBack ? window.flowBack() : false", result -> {
            if (!"true".equals(result)) finish();
        });
    }

    @Override public void onBackPressed() { handleBack(); }

    private void hideNavigationBar() {
        if (isFinishing() || isDestroyed()) return;
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                controller.hide(WindowInsets.Type.navigationBars());
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION);
        }
    }

    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideNavigationBar();
    }

    private void peerForeground(boolean active) {
        String root = dataDirectory.getAbsolutePath();
        peerLife.execute(() -> Python.getInstance().getModule("mobile.peers").callAttr("lifecycle", root, active));
    }

    @Override protected void onStart() {
        super.onStart();
        peerForeground(true);
    }

    @Override protected void onStop() {
        peerForeground(false);
        super.onStop();
    }

    @Override protected void onPause() {
        if (pageReady) web.evaluateJavascript("window.flowPause && window.flowPause()", null);
        web.onPause();
        super.onPause();
    }

    @Override protected void onResume() {
        super.onResume();
        if (web != null) {
            web.onResume();
            if (pageReady) web.evaluateJavascript("window.flowResume && window.flowResume()", null);
        }
        hideNavigationBar();
    }

    @Override protected void onDestroy() {
        replies.clear();
        web.removeJavascriptInterface("FlowNative");
        web.destroy();
        super.onDestroy();
    }
}
