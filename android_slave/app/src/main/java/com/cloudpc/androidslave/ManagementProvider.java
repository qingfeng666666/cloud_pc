package com.cloudpc.androidslave;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

/** Same-device, same-signature IPC; no extra network listener. */
public final class ManagementProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }
    @Override public Bundle call(String method, String arg, Bundle extras) {
        getContext().enforceCallingOrSelfPermission(
            "com.cloudpc.androidslave.permission.MANAGE_LOCAL", "Management requires same signature");
        try {
            String raw = extras == null ? "{}" : extras.getString("body", "{}");
            if (raw == null || raw.getBytes(StandardCharsets.UTF_8).length > 32768)
                throw new IllegalArgumentException("Management body too large");
            LocalManagementServer bridge = LocalManagementServer.active;
            JSONObject result = bridge == null ? LocalManagementServer.error("请先启动本机云电脑从机")
                : bridge.dispatch(method, new JSONObject(raw));
            // Use a pipe so screenshots cannot overflow Binder's transaction buffer.
            byte[] bytes = result.toString().getBytes(StandardCharsets.UTF_8);
            android.os.ParcelFileDescriptor[] pipe = android.os.ParcelFileDescriptor.createPipe();
            Bundle response = new Bundle();
            response.putParcelable("response_fd", pipe[0]);
            new Thread(() -> {
                try (java.io.OutputStream out =
                        new android.os.ParcelFileDescriptor.AutoCloseOutputStream(pipe[1])) {
                    out.write(bytes);
                } catch (java.io.IOException ignored) { }
            }, "management-response").start();
            return response;
        } catch (Exception ex) {
            Bundle response = new Bundle();
            response.putString("result", LocalManagementServer.error(
                ex.getClass().getSimpleName() + ": " + ex.getMessage()).toString());
            return response;
        }
    }
    @Override public Cursor query(Uri uri, String[] projection, String selection,
            String[] args, String sortOrder) { throw new UnsupportedOperationException(); }
    @Override public String getType(Uri uri) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
