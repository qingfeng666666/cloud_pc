package com.cloudpc.androidslave;

import android.content.Context;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;

/** Metadata-only persistent diagnostics available even if CLDC never starts. */
final class SlaveRuntimeLog {
    private static File file;
    static synchronized void init(Context context) {
        File base = context.getExternalFilesDir(null);
        if (base == null) base = context.getFilesDir();
        File root = new File(base, "shared");
        root.mkdirs();
        file = new File(root, "slave_runtime.log");
    }
    static String redact(String text) {
        return text.replaceAll("[A-Za-z0-9+/]{43}=", "[REDACTED_KEY]")
            .replaceAll("(?im)(PrivateKey|PresharedKey)\\s*=.*", "$1=[REDACTED]");
    }
    static synchronized void record(String event, String detail, Throwable failure) {
        if (file == null) return;
        try {
            if (file.length() > 1024 * 1024) {
                File previous = new File(file.getParentFile(), "slave_runtime.previous.log");
                if (previous.exists() && !previous.delete()) throw new IOException("Log rotation failed");
                if (!file.renameTo(previous)) throw new IOException("Log rotation failed");
            }
            JSONObject item = new JSONObject().put("time_ms", System.currentTimeMillis())
                .put("version", BuildConfig.VERSION_NAME).put("pid", android.os.Process.myPid())
                .put("thread", Thread.currentThread().getName()).put("event", event)
                .put("detail", redact(detail));
            if (failure != null) {
                StringWriter stack = new StringWriter(); failure.printStackTrace(new PrintWriter(stack));
                item.put("exception", redact(stack.toString()));
            }
            try (FileOutputStream out = new FileOutputStream(file, true)) {
                out.write((item.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ex) { android.util.Log.e("CloudSlaveLog", "Cannot persist runtime log", ex); }
    }
    static synchronized String tail() throws IOException {
        if (file == null || !file.exists()) return "暂无运行日志";
        try (RandomAccessFile input = new RandomAccessFile(file, "r")) {
            input.seek(Math.max(0, input.length() - 65536));
            byte[] bytes = new byte[(int)(input.length() - input.getFilePointer())];
            input.readFully(bytes);
            return new String(bytes, StandardCharsets.UTF_8);
        }
    }
    static synchronized String path() { return file == null ? "" : file.getAbsolutePath(); }
}
