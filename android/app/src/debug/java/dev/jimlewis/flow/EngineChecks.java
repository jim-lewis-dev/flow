package dev.jimlewis.flow;

import android.app.Activity;
import android.app.Instrumentation;
import android.os.Bundle;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/** Run the same portable adapter tests inside Android's packaged Python runtime. */
public final class EngineChecks extends Instrumentation {
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            runOnMainSync(() -> {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getTargetContext()));
            });
            Python py = Python.getInstance();
            py.getModule("tempfile").put("tempdir", PyObject.fromJava(getTargetContext().getCacheDir().getAbsolutePath()));
            PyObject unittest = py.getModule("unittest");
            PyObject suite = unittest.callAttr("TestSuite");
            for (String module : new String[]{"test_mobile", "test_mobile_sync", "test_dates"}) {
                suite.callAttr("addTests", unittest.get("defaultTestLoader")
                        .callAttr("loadTestsFromModule", py.getModule(module)));
            }
            PyObject run = unittest.callAttr("TextTestRunner").callAttr("run", suite);
            boolean ok = run.callAttr("wasSuccessful").toBoolean();
            result.putInt("tests", run.get("testsRun").toInt());
            result.putBoolean("success", ok);
            result.putString("errors", run.get("errors").toString());
            result.putString("failures", run.get("failures").toString());
            finish(ok ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
        } catch (Exception error) {
            result.putString("error", error.toString());
            finish(Activity.RESULT_CANCELED, result);
        }
    }
}
