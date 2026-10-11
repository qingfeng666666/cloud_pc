package com.cloudpc.management;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.LinkedHashMap;
import java.util.Map;

/** Editable JSON beside application data, never in the read-only APK directory. */
final class JsonSettings {
    final File file;
    private JSONObject values;
    JsonSettings(Context context, String name, JSONObject defaults, SharedPreferences previous) throws Exception {
        File directory = context.getExternalFilesDir(null);
        if (directory == null) directory = context.getFilesDir();
        file = new File(directory, name);
        if (file.exists()) {
            values = readValues();
        } else {
            values = new JSONObject(defaults.toString());
            java.util.Iterator<String> keys = defaults.keys();
            while (previous != null && keys.hasNext()) {
                String key = keys.next();
                Object old = previous.getAll().get(key);
                if (old != null) values.put(key, old);
            }
            persist();
        }
    }
    synchronized String getString(String key, String fallback) { return values.optString(key, fallback); }
    synchronized boolean getBoolean(String key, boolean fallback) { return values.optBoolean(key, fallback); }
    synchronized int getInt(String key, int fallback) { return values.optInt(key, fallback); }
    Editor edit() { return new Editor(); }
    private JSONObject readValues() throws IOException {
        try {
            return new JSONObject(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        } catch (org.json.JSONException error) {
            // Android JSONException may embed the entire document (including VPN keys).
            throw new IOException("配置 JSON 格式错误：" + file);
        }
    }
    private void persist() throws Exception {
        File temporary = File.createTempFile(".config-", ".tmp", file.getParentFile());
        try {
            try (FileOutputStream out = new FileOutputStream(temporary)) {
                out.write((values.toString(2) + "\n").getBytes(StandardCharsets.UTF_8));
                out.getFD().sync();
            }
            Files.move(temporary.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING);
        } finally { if (temporary.exists()) temporary.delete(); }
    }
    final class Editor {
        final Map<String,Object> changes = new LinkedHashMap<>();
        Editor putString(String key, String value) { changes.put(key, value); return this; }
        Editor putBoolean(String key, boolean value) { changes.put(key, value); return this; }
        Editor remove(String key) { changes.put(key, null); return this; }
        void apply() {
            synchronized (JsonSettings.this) {
                try {
                    JSONObject updated = readValues();
                    for (Map.Entry<String,Object> change : changes.entrySet()) {
                        if (change.getValue() == null) updated.remove(change.getKey());
                        else updated.put(change.getKey(), change.getValue());
                    }
                    JSONObject old = values;
                    values = updated;
                    try { persist(); } catch (Exception failure) { values = old; throw failure; }
                } catch (Exception error) { throw new IllegalStateException("无法保存配置：" + file, error); }
            }
        }
    }
}
