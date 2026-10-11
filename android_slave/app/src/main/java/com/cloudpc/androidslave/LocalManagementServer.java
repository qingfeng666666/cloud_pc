package com.cloudpc.androidslave;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.net.Socket;
import java.net.InetSocketAddress;
import java.util.*;
import java.util.concurrent.*;

/** Local UI bridge, not a second cross-device connection. */
final class LocalManagementServer implements AutoCloseable {
    interface Sender { void send(JSONObject request) throws Exception; }
    final Map<Object, Sender> senders = new LinkedHashMap<>();
    final Map<String, CompletableFuture<JSONObject>> pending = new HashMap<>();
    final Set<Object> hosts = new HashSet<>();
    static volatile LocalManagementServer active;
    boolean running;
    long accepted, parsed, pushed, delivered, expired;
    String lastStage = "initial", lastError = "";
    final File diagnosticFile;
    final JsonSettings settings;
    final int hostRegistrationPort;
    final int slavePort;
    volatile String hostAddress;
    LocalManagementServer(android.content.Context context, File root, int port) throws IOException {
        diagnosticFile = new File(root, "management_diagnostics.json");
        try {
            settings = new JsonSettings(context, "slave.json", new JSONObject()
                .put("host_ip", "10.0.0.2").put("host_registration_port", 19526), null);
        } catch (Exception error) { throw new IOException("无法读取 slave.json", error); }
        hostAddress = settings.getString("host_ip", "10.0.0.2");
        hostRegistrationPort = settings.getInt("host_registration_port", 19526);
        slavePort = port;
    }

    JSONObject register(String operation, JSONObject body) {
        try (Socket socket = new Socket()) {
            String target = body.optString("host_ip", hostAddress).trim();
            if (!android.util.Patterns.IP_ADDRESS.matcher(target).matches() || target.contains(":"))
                return error("主机 IP 必须是有效的 IPv4 地址");
            socket.connect(new InetSocketAddress(target, hostRegistrationPort), 5000);
            socket.setSoTimeout(5000);
            byte[] data = new JSONObject().put("operation", operation)
                .put("session_id", body.optString("session_id"))
                .put("slave_port", slavePort).toString().getBytes(StandardCharsets.UTF_8);
            DataOutputStream out = new DataOutputStream(socket.getOutputStream());
            out.writeInt(0x434c4443); out.writeShort(1); out.writeShort(0x0701);
            out.writeInt(data.length); out.write(data); out.flush();
            DataInputStream in = new DataInputStream(socket.getInputStream());
            if (in.readInt() != 0x434c4443 || in.readUnsignedShort() != 1 || in.readUnsignedShort() != 0x0781)
                throw new IOException("Invalid registration response");
            int size = in.readInt();
            if (size <= 0 || size > 4096) throw new IOException("Invalid registration length");
            byte[] result = new byte[size]; in.readFully(result);
            JSONObject response = new JSONObject(new String(result, StandardCharsets.UTF_8));
            synchronized (this) { record("registration_" + operation, response.optString("error", "")); }
            return response;
        } catch (Exception ex) {
            synchronized (this) { record("registration_failed", ex.toString()); }
            return error("管理注册失败：" + ex.getMessage());
        }
    }

    synchronized JSONObject diagnostics() {
        try {
            return new JSONObject().put("ok", true).put("time_ms", System.currentTimeMillis())
                .put("accepted", accepted).put("parsed", parsed).put("pushed", pushed)
                .put("delivered", delivered).put("expired", expired)
                .put("pending", pending.size()).put("hosts", hosts.size())
                .put("stage", lastStage).put("error", lastError)
                .put("provider_ready", running).put("transport", "binder");
        } catch (Exception ex) { return error(ex.getMessage()); }
    }
    synchronized void record(String stage, String failure) {
        lastStage = stage; if (failure != null) lastError = failure;
        try (FileOutputStream out = new FileOutputStream(diagnosticFile)) {
            out.write(diagnostics().toString().getBytes(StandardCharsets.UTF_8));
        } catch (IOException ex) { android.util.Log.w("CloudAndroidSlave", "management diagnostic", ex); }
    }
    JSONObject selfProbe() { return diagnostics(); }

    synchronized void connected(Object owner, Sender sender) {
        hosts.add(owner);
        senders.put(owner, sender);
        if (owner instanceof Socket) {
            hostAddress = ((Socket) owner).getInetAddress().getHostAddress();
        settings.edit().putString("host_ip", hostAddress).apply();
        }
        record("host_connected", null);
    }
    synchronized void disconnected(Object owner) {
        hosts.remove(owner);
        senders.remove(owner);
        record("host_disconnected", null);
        if (hosts.isEmpty()) {
            for (CompletableFuture<JSONObject> future : pending.values())
                future.complete(error("主机连接已断开；未自动重发"));
        }
    }
    static JSONObject error(String message) {
        try { return new JSONObject().put("ok", false).put("error", message); }
        catch (Exception ex) { throw new IllegalStateException(ex); }
    }
    synchronized void resolve(JSONObject packet) {
        CompletableFuture<JSONObject> future = pending.get(packet.optString("request_id"));
        JSONObject result = packet.optJSONObject("result");
        if (future != null && result != null) { future.complete(result); delivered++; record("delivered", null); }
    }
    JSONObject request(String operation, JSONObject body) throws Exception {
        String id = UUID.randomUUID().toString().replace("-", "");
        CompletableFuture<JSONObject> future = new CompletableFuture<>();
        Sender sender;
        synchronized (this) {
            if (senders.isEmpty()) return error("本机从机尚未连接支持主动请求的云电脑主机");
            if (pending.size() >= 16) return error("管理请求队列已满");
            pending.put(id, future);
            sender = senders.values().iterator().next();
        }
        try {
            sender.send(new JSONObject().put("event", "host_management_request").put("send_id", id)
                .put("operation", operation).put("payload", body));
            synchronized (this) { pushed++; record("pushed_" + operation, null); }
            return future.get(30, TimeUnit.SECONDS);
        }
        catch (TimeoutException ex) { synchronized (this) { expired++; record("expired", "host_result_timeout"); } return error("主机结果未确认；未自动重发"); }
        catch (Exception ex) {
            synchronized (this) { record("push_failed", ex.toString()); }
            return error("管理请求发送失败；未自动重发：" + ex.getMessage());
        }
        finally { synchronized (this) {
            pending.remove(id);
        } }
    }
    JSONObject dispatch(String operation, JSONObject body) throws Exception {
        synchronized (this) {
            accepted++; parsed++; record("binder_" + operation, "");
            if ("health".equals(operation))
                return new JSONObject().put("ok", true).put("transport", "binder")
                    .put("host_connected", !hosts.isEmpty()).put("diagnostics", diagnostics());
        }
        if ("management_activate".equals(operation)) return register("activate", body);
        if ("management_release".equals(operation)) return register("release", body);
        if (!Arrays.asList("current", "send", "stop", "slaves", "screenshot", "activity",
                "llm_providers", "llm_sessions", "llm_create", "llm_open", "llm_current", "llm_send", "llm_stop").contains(operation))
            return error("Unsupported local management operation");
        return request(operation, body);
    }
    void start() throws IOException { running = true; active = this; record("binder_ready", ""); }
    @Override public void close() {
        synchronized (this) {
            running = false;
            if (active == this) active = null;
            for (Object owner : new ArrayList<>(hosts)) disconnected(owner);
        }
    }
}
