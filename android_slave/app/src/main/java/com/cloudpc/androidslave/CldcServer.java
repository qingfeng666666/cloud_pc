package com.cloudpc.androidslave;

import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** CLDC v1 listener. Remote execution remains unsupported on Android. */
final class CldcServer implements AutoCloseable {
    private static final String TAG = "CloudAndroidSlave";
    private static final int MAX_JSON = 2 * 1024 * 1024;
    private final InetAddress bindAddress;
    private final int port;
    private final File root;
    private final String name;
    private final HoloLensPortalRelay portalRelay;
    private final ExecutorService clients = Executors.newFixedThreadPool(4);
    private final Set<Socket> activeSockets = Collections.newSetFromMap(new ConcurrentHashMap<>());
    private volatile ServerSocket listener;
    private final LocalManagementServer management;

    private final android.content.Context context;
    CldcServer(android.content.Context context, InetAddress bindAddress, int port, File root, File cacheDir,
               String name) throws IOException {
        this.context = context;
        this.bindAddress = bindAddress;
        this.port = port;
        this.root = root.getCanonicalFile();
        this.name = name;
        if (!this.root.isDirectory() && !this.root.mkdirs()) throw new IOException("无法创建共享目录");
        this.management = new LocalManagementServer(context, this.root, port);
        this.portalRelay = new HoloLensPortalRelay(cacheDir);
    }

    void start() throws IOException {
        listener = new ServerSocket(port, 8, bindAddress);
        try { management.start(); }
        catch (IOException ex) { listener.close(); throw ex; }
        Thread acceptor = new Thread(() -> {
            while (!listener.isClosed()) {
                try {
                    Socket socket = listener.accept();
                    socket.setSoTimeout(120_000);
                    activeSockets.add(socket);
                    clients.execute(() -> serve(socket));
                } catch (IOException error) {
                    if (!listener.isClosed()) {
                        Log.w(TAG, "accept", error);
                        SlaveRuntimeLog.record("accept_failed", "port=" + port, error);
                    }
                }
            }
        }, "cldc-accept");
        acceptor.setDaemon(true);
        acceptor.start();
        Log.i(TAG, "CLDC listening on " + bindAddress.getHostAddress() + ":" + port);
        SlaveRuntimeLog.record("cldc_listening", bindAddress.getHostAddress() + ":" + port, null);
    }

    private void serve(Socket socket) {
        String peer = socket.getInetAddress().getHostAddress();
        SlaveRuntimeLog.record("peer_accepted", "ip=" + peer, null);
        try (socket;
             DataInputStream in = new DataInputStream(new BufferedInputStream(socket.getInputStream()));
             DataOutputStream out = new DataOutputStream(new BufferedOutputStream(socket.getOutputStream()))) {
            Frame first = read(in);
            if (first.type != 0x0001) {
                send(out, 0x0fff, error("BAD_HANDSHAKE", "Expected handshake request"));
                return;
            }
            send(out, 0x0002, new JSONObject().put("slave_name", name).put("message", "connected")
                    .put("app_version", BuildConfig.VERSION_NAME)
                    .put("capabilities", new JSONArray().put("management_bridge_v1").put("management_push_v1")));
            JSONArray hostCaps = first.payload.optJSONArray("capabilities");
            boolean push = false;
            if (hostCaps != null) for (int i = 0; i < hostCaps.length(); i++)
                if ("management_push_v1".equals(hostCaps.optString(i))) push = true;
            if ("Host".equals(first.payload.optString("host_name")) && push)
                management.connected(socket, request -> send(out, 0x0607, request));
            SlaveRuntimeLog.record("handshake_complete", "ip=" + peer, null);
            while (!socket.isClosed()) {
                Frame frame = read(in);
                if (frame.type == 0x00ff) return;
                try { dispatch(frame, out); }
                catch (Exception error) {
                    Log.w(TAG, "CLDC request type=" + frame.type, error);
                    SlaveRuntimeLog.record("request_failed", "ip=" + peer + " type=" + frame.type, error);
                    send(out, 0x0fff, error("REQUEST_FAILED", error.getMessage()));
                }
            }
        } catch (IOException error) {
            Log.i(TAG, "CLDC peer disconnected: " + error.getMessage());
            SlaveRuntimeLog.record("peer_io_closed", "ip=" + peer, error);
        } catch (Exception error) {
            Log.e(TAG, "CLDC connection", error);
            SlaveRuntimeLog.record("peer_failed", "ip=" + peer, error);
        } finally {
            activeSockets.remove(socket);
            management.disconnected(socket);
            SlaveRuntimeLog.record("peer_disconnected", "ip=" + peer, null);
        }
    }

    private void dispatch(Frame frame, DataOutputStream out) throws Exception {
        JSONObject request = frame.payload;
        String path = request.optString("path", "/");
        switch (frame.type) {
            case 0x0003:
                send(out, 0x0003, new JSONObject().put("echo", request.opt("echo")));
                break;
            case 0x0101:
                File dir = safePath(path);
                if (!dir.isDirectory()) throw new IOException("Not a directory: " + path);
                JSONArray entries = new JSONArray();
                File[] files = dir.listFiles();
                if (files == null) throw new IOException("Cannot list: " + path);
                Arrays.sort(files, Comparator.comparing(File::isFile).thenComparing(File::getName));
                for (File file : files) entries.put(metadata(file));
                send(out, 0x0181, new JSONObject().put("path", path).put("entries", entries));
                break;
            case 0x0104:
                File target = safePath(path);
                if (!target.exists()) throw new IOException("Not found: " + path);
                JSONObject stat = metadata(target).put("path", path).put("ctime", target.lastModified() / 1000);
                send(out, 0x0185, stat);
                break;
            case 0x0102:
                download(path, safePath(path), out);
                break;
            case 0x0108:
                mutate(request, out);
                break;
            case 0x0402:
                send(out, 0x0482, portalRelay.handle(request));
                break;
            case 0x0606:
                management.resolve(request);
                send(out, 0x0686, new JSONObject().put("ok", true));
                break;
            case 0x0605:
                JSONObject diagnostic = new JSONObject().put("ok", true)
                    .put("app_version", BuildConfig.VERSION_NAME).put("slave_runtime_log", "slave_runtime.log");
                try {
                    android.os.Bundle logs = context.getContentResolver().call(
                        android.net.Uri.parse("content://com.cloudpc.management.logs"), "logs", null, null);
                    if (logs == null || logs.containsKey("error"))
                        throw new IOException(logs == null ? "No log provider result" : logs.getString("error"));
                    byte[] bytes = logs.getString("text", "").getBytes(StandardCharsets.UTF_8);
                    File logTarget = new File(root, "management_ui.log");
                    File temporary = File.createTempFile(".management_ui_", ".tmp", root);
                    try {
                        try (FileOutputStream file = new FileOutputStream(temporary)) { file.write(bytes); }
                        Files.move(temporary.toPath(), logTarget.toPath(), StandardCopyOption.REPLACE_EXISTING);
                    } finally { if (temporary.exists()) temporary.delete(); }
                    diagnostic.put("management_ui_log", "management_ui.log").put("management_ui_log_bytes", bytes.length);
                } catch (Exception ex) { diagnostic.put("management_ui_log_error", ex.toString()); }
                diagnostic.put("management", request.optBoolean("local_management_probe", false)
                    ? management.selfProbe() : management.diagnostics());
                send(out, 0x0685, diagnostic);
                break;
            default:
                send(out, 0x0fff, error("UNSUPPORTED_ON_ANDROID",
                        "Android slave does not support this CLDC message type"));
        }
    }

    private JSONObject metadata(File file) throws Exception {
        return new JSONObject().put("name", file.getName()).put("size", file.length())
                .put("type", file.isDirectory() ? "dir" : "file")
                .put("mtime", file.lastModified() / 1000);
    }

    private void download(String path, File file, DataOutputStream out) throws Exception {
        if (!file.isFile()) throw new IOException("Not a file: " + path);
        long size = file.length();
        int total = (int) Math.max(1, (size + 65535) / 65536);
        if (total > 65536) throw new IOException("File too large for this protocol implementation");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536];
            for (int i = 0; i < total; i++) {
                int count = input.read(buffer);
                if (count < 0) count = 0;
                send(out, 0x0182, new JSONObject().put("path", path).put("chunk", i)
                        .put("total", total).put("size", count)
                        .put("data", Base64.encodeToString(buffer, 0, count, Base64.NO_WRAP)));
            }
        }
        send(out, 0x0183, new JSONObject().put("path", path).put("total_chunks", total)
                .put("total_size", size));
    }

    private void mutate(JSONObject request, DataOutputStream out) throws Exception {
        String path = request.optString("path", "");
        if (path.isEmpty()) throw new IOException("path is required");
        File file = safePath(path);
        if (file.equals(root)) throw new IOException("Cannot mutate root");
        String operation = request.optString("operation", "");
        switch (operation) {
            case "mkdir":
                if (!file.mkdir()) throw new IOException("mkdir failed");
                break;
            case "delete":
                if (!file.delete()) throw new IOException("delete failed or directory not empty");
                break;
            case "rename":
                String newName = request.optString("new_name", "");
                if (newName.isEmpty() || newName.contains("/") || newName.contains("\\"))
                    throw new IOException("new_name must be one filename");
                File destination = safePath(new File(file.getParentFile(), newName).getPath());
                if (destination.exists() || !file.renameTo(destination)) throw new IOException("rename failed");
                break;
            case "write_chunk":
                if (request.optInt("chunk", -1) != 0 || request.optInt("total", -1) != 1)
                    throw new IOException("MVP accepts one chunk per file (<=1 MiB)");
                byte[] bytes = Base64.decode(request.getString("data"), Base64.DEFAULT);
                if (bytes.length > 1024 * 1024) throw new IOException("chunk exceeds 1 MiB");
                String sha256 = request.optString("sha256", "");
                String actual = hex(MessageDigest.getInstance("SHA-256").digest(bytes));
                if (!sha256.isEmpty() && !sha256.equalsIgnoreCase(actual)) throw new IOException("SHA-256 mismatch");
                File temp = File.createTempFile(".cloud_mutate_", ".tmp", root);
                try {
                    try (FileOutputStream output = new FileOutputStream(temp)) { output.write(bytes); }
                    if (!file.getParentFile().isDirectory() && !file.getParentFile().mkdirs())
                        throw new IOException("Cannot create parent directory");
                    Files.move(temp.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING);
                } finally { if (temp.exists()) temp.delete(); }
                send(out, 0x0187, new JSONObject().put("operation", "write")
                        .put("path", path).put("status", "ok").put("sha256", actual));
                return;
            default:
                throw new IOException("Unsupported mutation operation");
        }
        send(out, 0x0187, new JSONObject().put("operation", operation)
                .put("path", path).put("status", "ok"));
    }

    private File safePath(String path) throws IOException {
        String relative = path == null ? "" : path.replace('\\', '/');
        while (relative.startsWith("/")) relative = relative.substring(1);
        File candidate = new File(root, relative).getCanonicalFile();
        if (!candidate.equals(root) && !candidate.getPath().startsWith(root.getPath() + File.separator))
            throw new IOException("Path outside shared root");
        return candidate;
    }

    private static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) text.append(String.format("%02x", value & 255));
        return text.toString();
    }

    private static JSONObject error(String code, String message) throws Exception {
        return new JSONObject().put("code", code).put("message", message == null ? "" : message);
    }

    private static Frame read(DataInputStream in) throws Exception {
        byte[] magic = new byte[4];
        in.readFully(magic);
        if (!Arrays.equals(magic, new byte[]{'C', 'L', 'D', 'C'})) throw new IOException("Bad CLDC magic");
        int version = in.readUnsignedShort();
        int type = in.readUnsignedShort();
        int size = in.readInt();
        if (version != 1 || size < 0 || size > MAX_JSON) throw new IOException("Invalid CLDC frame");
        byte[] json = new byte[size];
        in.readFully(json);
        return new Frame(type, size == 0 ? new JSONObject() :
                new JSONObject(new String(json, StandardCharsets.UTF_8)));
    }

    private static void send(DataOutputStream out, int type, JSONObject payload) throws Exception {
        byte[] json = payload.toString().getBytes(StandardCharsets.UTF_8);
        if (json.length > MAX_JSON) throw new IOException("Response too large");
        synchronized (out) {
            out.write(new byte[]{'C', 'L', 'D', 'C'});
            out.writeShort(1);
            out.writeShort(type);
            out.writeInt(json.length);
            out.write(json);
            out.flush();
        }
    }

    @Override public void close() {
        SlaveRuntimeLog.record("cldc_close", "port=" + port, null);
        management.close();
        try { if (listener != null) listener.close(); }
        catch (IOException error) { Log.w(TAG, "close", error); }
        for (Socket socket : activeSockets) {
            try { socket.close(); }
            catch (IOException error) { Log.w(TAG, "close client", error); }
        }
        clients.shutdownNow();
        portalRelay.close();
    }

    private static final class Frame {
        final int type;
        final JSONObject payload;
        Frame(int type, JSONObject payload) { this.type = type; this.payload = payload; }
    }
}
