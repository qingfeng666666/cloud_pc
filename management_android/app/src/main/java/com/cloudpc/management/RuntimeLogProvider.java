package com.cloudpc.management;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;

/** Read-only diagnostics export to the same-signature slave, independent of request bridge. */
public final class RuntimeLogProvider extends ContentProvider {
    @Override public boolean onCreate() { RuntimeLog.init(getContext()); return true; }
    @Override public Bundle call(String method, String arg, Bundle extras) {
        getContext().enforceCallingOrSelfPermission(
            "com.cloudpc.androidslave.permission.MANAGE_LOCAL", "Same signature required");
        if (!"logs".equals(method)) throw new IllegalArgumentException("Only logs supported");
        Bundle result = new Bundle();
        try { result.putString("text", RuntimeLog.snapshot()); }
        catch (Exception ex) { result.putString("error", ex.toString()); }
        return result;
    }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { throw new UnsupportedOperationException(); }
    @Override public String getType(Uri u) { return null; }
    @Override public Uri insert(Uri u, ContentValues v) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri u, String s, String[] a) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { throw new UnsupportedOperationException(); }
}
