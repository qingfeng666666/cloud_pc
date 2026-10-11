package com.cloudpc.androidslave;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;

import com.wireguard.android.backend.GoBackend;
import com.wireguard.android.backend.Tunnel;
import com.wireguard.config.Config;

import java.io.ByteArrayInputStream;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class SlaveService extends Service {
    public static final String ACTION_START_WIREGUARD = "com.cloudpc.androidslave.START_WIREGUARD";
    public static final String ACTION_START_SLAVE = "com.cloudpc.androidslave.START_SLAVE";
    public static final String ACTION_STOP_WIREGUARD = "com.cloudpc.androidslave.STOP_WIREGUARD";
    public static final String ACTION_STOP_SLAVE = "com.cloudpc.androidslave.STOP_SLAVE";
    private static final String CHANNEL = "cloud_slave";
    private static final String TAG = "CloudAndroidSlave";
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private GoBackend backend;
    private Tunnel tunnel;
    private CldcServer server;
    private HandCsvReceiver handReceiver;
    private AlxrLogReceiver logReceiver;
    private String vpnIp = "";
    private int slavePort = 9527;

    @Override public void onCreate() {
        super.onCreate();
        SlaveRuntimeLog.init(this);
        SlaveRuntimeLog.record("service_create", "", null);
        getSystemService(NotificationManager.class).createNotificationChannel(
                new NotificationChannel(CHANNEL, "云电脑从机", NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        startForeground(1, notification("正在启动"));
        SlaveRuntimeLog.record("service_command", "action=" + (intent == null ? "null" : intent.getAction()) + " startId=" + startId, null);
        if (intent == null) {
            worker.execute(this::stopEverything);
            return START_NOT_STICKY;
        }
        String configText = intent.getStringExtra("config");
        String name = intent.getStringExtra("name");
        String action = intent.getAction();
        worker.execute(() -> {
            if (ACTION_START_WIREGUARD.equals(action)) startWireGuard(configText);
            else if (ACTION_START_SLAVE.equals(action)) startSlave(name);
            else if (ACTION_STOP_WIREGUARD.equals(action)) { stopWireGuard(); updateStatus(""); }
            else if (ACTION_STOP_SLAVE.equals(action)) { stopSlave(); updateStatus(""); }
            else updateStatus("未知操作");
            if (backend == null && server == null && stopSelfResult(startId)) {
                stopForeground(STOP_FOREGROUND_REMOVE);
            }
        });
        return START_NOT_STICKY;
    }

    private void startWireGuard(String text) {
        if (backend != null && tunnel != null) { updateStatus("WireGuard 已启动"); return; }
        try {
            if (text == null) throw new IllegalArgumentException("缺少 WireGuard 配置");
            String ip = interfaceIpv4(text);
            Config config = Config.parse(new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8)));
            SlaveRuntimeLog.record("config_parsed", "ip=" + ip, null);
            backend = new GoBackend(getApplicationContext());
            tunnel = new Tunnel() {
                @Override public String getName() { return "cloudslave"; }
                @Override public void onStateChange(State newState) {
                    Log.i(TAG, "WireGuard " + newState);
                    SlaveRuntimeLog.record("wireguard_state", newState.toString(), null);
                }
            };
            backend.setState(tunnel, Tunnel.State.UP, config);
            vpnIp = ip;
            SlaveRuntimeLog.record("wireguard_up", "ip=" + ip, null);
            updateStatus("");
        } catch (Exception error) {
            SlaveRuntimeLog.record("wireguard_start_failed", "", error);
            stopWireGuard();
            updateStatus("VPN 启动失败：" + error.getMessage());
        }
    }

    private void startSlave(String name) {
        if (server != null) { updateStatus("从机已启动"); return; }
        String stage = "cldc_9527";
        try {
            JsonSettings settings = new JsonSettings(this, "slave.json", new org.json.JSONObject()
                .put("name", "AndroidSlave").put("port", 9527).put("root", "shared"), null);
            slavePort = settings.getInt("port", 9527);
            if (slavePort < 1 || slavePort > 65535) throw new IllegalArgumentException("port 必须为1至65535");
            java.io.File shared = new java.io.File(settings.getString("root", "shared"));
            if (!shared.isAbsolute()) shared = new java.io.File(settings.file.getParentFile(), shared.getPath());
            name = settings.getString("name", name == null ? "AndroidSlave" : name);
            server = new CldcServer(getApplicationContext(), InetAddress.getByName("0.0.0.0"), slavePort,
                    shared,
                    getCacheDir(),
                    name == null || name.isEmpty() ? "AndroidSlave" : name);
            server.start();
            stage = "recording_9541";
            handReceiver = new HandCsvReceiver(shared);
            handReceiver.start();
            SlaveRuntimeLog.record("recording_listening", "TCP 9541", null);
            stage = "alxr_logs_9999";
            logReceiver = new AlxrLogReceiver(shared);
            logReceiver.start();
            SlaveRuntimeLog.record("startup_ready", "ip=0.0.0.0 TCP " + slavePort + "/9541 UDP 9999", null);
            updateStatus("");
        } catch (Exception error) {
            Log.e(TAG, "start failed", error);
            SlaveRuntimeLog.record("startup_failed", "stage=" + stage, error);
            stopSlave();
            updateStatus("从机启动失败：" + error.getMessage());
        }
    }

    private static String interfaceIpv4(String text) {
        boolean inInterface = false;
        Pattern address = Pattern.compile("(?i)^Address\\s*=\\s*([0-9]{1,3}(?:\\.[0-9]{1,3}){3})(?:/\\d+)?(?:\\s*,.*)?$");
        for (String raw : text.split("\\R")) {
            String line = raw.trim();
            if (line.startsWith("[")) inInterface = line.equalsIgnoreCase("[Interface]");
            if (inInterface) {
                Matcher match = address.matcher(line);
                if (match.matches()) return match.group(1);
            }
        }
        throw new IllegalArgumentException("[Interface] 需要 IPv4 Address");
    }

    private void stopSlave() {
        SlaveRuntimeLog.record("slave_stop", "", null);
        if (logReceiver != null) { logReceiver.close(); logReceiver = null; }
        if (handReceiver != null) { handReceiver.close(); handReceiver = null; }
        if (server != null) { server.close(); server = null; }
    }

    private void stopWireGuard() {
        if (backend != null && tunnel != null) {
            try { backend.setState(tunnel, Tunnel.State.DOWN, null); }
            catch (Exception error) { Log.w(TAG, "WireGuard stop", error); SlaveRuntimeLog.record("wireguard_stop_failed", "", error); }
        }
        backend = null;
        tunnel = null;
        vpnIp = "";
        SlaveRuntimeLog.record("wireguard_stopped", "", null);
    }

    private void updateStatus(String message) {
        notifyStatus((backend != null ? "VPN " + vpnIp : "VPN 未启动") + " · " +
                (server != null ? "从机 " + slavePort + " · CSV 9541 · 日志 9999" : "从机未启动") +
                (message.isEmpty() ? "" : " · " + message));
    }

    private void stopEverything() {
        SlaveRuntimeLog.record("user_stop", "", null);
        stopSlave();
        stopWireGuard();
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private Notification notification(String text) {
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("云电脑 Android 从机")
                .setContentText(text)
                .setOngoing(true).build();
    }

    private void notifyStatus(String text) {
        getSystemService(NotificationManager.class).notify(1, notification(text));
    }

    @Override public void onDestroy() {
        SlaveRuntimeLog.record("service_destroy", "", null);
        worker.execute(() -> { stopSlave(); stopWireGuard(); });
        worker.shutdown();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }

}
