package com.cloudpc.management;

import android.content.Context;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;

final class RuntimeLog {
    static File file;
    static synchronized void init(Context context) {
        File base = context.getExternalFilesDir(null);
        if (base == null) base = context.getFilesDir();
        File directory = new File(base, "share");
        directory.mkdirs();
        file = new File(directory, "management_ui.log");
    }
    static synchronized void write(String event, String detail, Throwable failure) {
        if (file == null) return;
        try {
            if (file.length() > 256 * 1024) {
                File previous = new File(file.getParentFile(), "management_ui.previous.log");
                if (previous.exists() && !previous.delete()) throw new IOException("Cannot rotate log");
                if (!file.renameTo(previous)) throw new IOException("Cannot rotate log");
            }
            JSONObject record = new JSONObject().put("time_ms", System.currentTimeMillis())
                .put("version", BuildConfig.VERSION_NAME).put("event", event).put("detail", detail);
            if (failure != null) {
                StringWriter stack = new StringWriter();
                failure.printStackTrace(new PrintWriter(stack));
                record.put("exception", stack.toString());
            }
            try (FileOutputStream out = new FileOutputStream(file, true)) {
                out.write((record.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ex) { android.util.Log.e("CloudManagement", "Cannot persist runtime log", ex); }
    }
    static synchronized String snapshot() throws IOException {
        if (file == null || !file.isFile()) return "";
        try (InputStream in = new FileInputStream(file); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] bytes = new byte[8192]; int count;
            while ((count = in.read(bytes)) != -1 && out.size() < 256 * 1024)
                out.write(bytes, 0, Math.min(count, 256 * 1024 - out.size()));
            return out.toString("UTF-8");
        }
    }
}
