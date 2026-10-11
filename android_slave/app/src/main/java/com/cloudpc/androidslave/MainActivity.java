package com.cloudpc.androidslave;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.net.VpnService;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private static final int VPN_REQUEST = 1001;
    private EditText config;
    private EditText name;
    private TextView status;
    private JsonSettings settings;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        SlaveRuntimeLog.init(this);
        SlaveRuntimeLog.record("activity_create", "", null);
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, failure) -> {
            SlaveRuntimeLog.record("uncaught_exception", "thread=" + thread.getName(), failure);
            if (previous != null) previous.uncaughtException(thread, failure);
        });
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (20 * getResources().getDisplayMetrics().density);
        box.setPadding(padding, padding, padding, padding);
        scroll.addView(box);
        TextView title = new TextView(this);
        title.setText("云电脑 Android 从机 " + BuildConfig.VERSION_NAME + " · WireGuard");
        title.setTextSize(22);
        box.addView(title);
        name = new EditText(this);
        name.setHint("从机名称");
        name.setSingleLine(true);
        name.setText("AndroidSlave");
        box.addView(name);
        config = new EditText(this);
        config.setHint("粘贴 WireGuard .conf（需含 Interface Address、PrivateKey 和 Peer）");
        config.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        config.setMinLines(3);
        config.setMaxLines(4);
        config.setGravity(android.view.Gravity.TOP | android.view.Gravity.START);
        config.setVerticalScrollBarEnabled(true);
        try {
            settings = new JsonSettings(this, "slave.json", new org.json.JSONObject()
                .put("name", "AndroidSlave").put("port", 9527).put("root", "shared")
                .put("host_ip", "10.0.0.2").put("host_registration_port", 19526)
                .put("wireguard_config", ""), getPreferences(0));
            name.setText(settings.getString("name", "AndroidSlave"));
            config.setText(settings.getString("wireguard_config", ""));
        } catch (Exception error) {
            SlaveRuntimeLog.record("config_load_failed", "slave.json", error);
            new android.app.AlertDialog.Builder(this).setTitle("配置读取失败（未覆盖文件）")
                .setMessage(error.toString()).setPositiveButton("关闭", (d,w) -> finish()).show();
            return;
        }
        box.addView(config);
        TextView configPath = new TextView(this);
        configPath.setText("配置文件：" + settings.file);
        configPath.setTextIsSelectable(true); box.addView(configPath);
        Button start = new Button(this);
        start.setText("启动 WireGuard");
        start.setOnClickListener(v -> startWithConsent());
        box.addView(start);
        Button startSlave = new Button(this);
        startSlave.setText("启动从机");
        startSlave.setOnClickListener(v -> startSlave());
        box.addView(startSlave);
        Button stopVpn = new Button(this);
        stopVpn.setText("停止 WireGuard");
        stopVpn.setOnClickListener(v -> {
            Intent intent = new Intent(this, SlaveService.class);
            intent.setAction(SlaveService.ACTION_STOP_WIREGUARD);
            startService(intent);
            status.setText("已请求停止 WireGuard，从机保持运行");
        });
        box.addView(stopVpn);
        Button stop = new Button(this);
        stop.setText("停止从机");
        stop.setOnClickListener(v -> {
            Intent intent = new Intent(this, SlaveService.class);
            intent.setAction(SlaveService.ACTION_STOP_SLAVE);
            startService(intent);
            status.setText("已请求停止从机，WireGuard 保持运行");
        });
        box.addView(stop);
        Button logs = new Button(this);
        logs.setText("查看从机运行日志");
        logs.setOnClickListener(v -> {
            TextView content = new TextView(this); content.setTextIsSelectable(true);
            ScrollView logScroll = new ScrollView(this); logScroll.addView(content);
            try { content.setText(SlaveRuntimeLog.path() + "\n\n" + SlaveRuntimeLog.tail()); }
            catch (Exception ex) { content.setText(ex.toString()); }
            new android.app.AlertDialog.Builder(this).setTitle("从机运行日志（最近64KB）")
                .setView(logScroll).setPositiveButton("关闭", null).show();
        });
        box.addView(logs);
        Button licenses = new Button(this);
        licenses.setText("开源许可与第三方声明");
        licenses.setOnClickListener(v -> {
            TextView content = new TextView(this);
            content.setTextIsSelectable(true);
            ScrollView licenseScroll = new ScrollView(this);
            licenseScroll.addView(content);
            StringBuilder text = new StringBuilder();
            try {
                String[] files = getAssets().list("licenses");
                if (files != null) {
                    java.util.Arrays.sort(files);
                    for (String file : files) {
                        text.append("\n===== ").append(file).append(" =====\n\n");
                        try (java.io.InputStream in = getAssets().open("licenses/" + file);
                             java.io.Reader reader = new java.io.InputStreamReader(in, java.nio.charset.StandardCharsets.UTF_8)) {
                            char[] buffer = new char[4096];
                            int count;
                            while ((count = reader.read(buffer)) != -1) text.append(buffer, 0, count);
                        }
                    }
                }
                content.setText(text.toString());
            } catch (Exception error) {
                content.setText("读取许可文件失败：" + error);
            }
            new android.app.AlertDialog.Builder(this).setTitle("开源许可与第三方声明")
                .setView(licenseScroll).setPositiveButton("关闭", null).show();
        });
        box.addView(licenses);
        status = new TextView(this);
        status.setText("WireGuard 与从机分别启动。从机监听可用网络的 9527 端口；VPN 未启动时也可使用局域网。文件根目录：Android/data/com.cloudpc.androidslave/files/shared");
        box.addView(status);
        setContentView(scroll);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != getPackageManager().PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1002);
        }
    }

    private void startWithConsent() {
        String text = config.getText().toString().trim();
        if (text.isEmpty()) { status.setText("请先粘贴 WireGuard 配置"); return; }
        if (!saveSettings()) return;
        Intent approval = VpnService.prepare(this);
        if (approval != null) startActivityForResult(approval, VPN_REQUEST);
        else startWireGuard();
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == VPN_REQUEST) {
            if (resultCode == RESULT_OK) startWireGuard();
            else status.setText("VPN 授权被取消；从机状态不变");
        }
    }

    private void startSlave() {
        if (!saveSettings()) return;
        Intent intent = new Intent(this, SlaveService.class);
        intent.setAction(SlaveService.ACTION_START_SLAVE);
        intent.putExtra("name", name.getText().toString().trim());
        startForegroundService(intent);
        status.setText("已请求启动从机（不启动 VPN）；请查看常驻通知中的状态");
    }

    private void startWireGuard() {
        Intent intent = new Intent(this, SlaveService.class);
        intent.setAction(SlaveService.ACTION_START_WIREGUARD);
        intent.putExtra("config", config.getText().toString());
        startForegroundService(intent);
        status.setText("已请求启动 WireGuard（不启动从机）；请查看常驻通知中的状态");
    }

    private boolean saveSettings() {
        try {
            settings.edit().putString("name", name.getText().toString().trim())
                .putString("wireguard_config", config.getText().toString()).apply();
            return true;
        } catch (Exception error) {
            status.setText("配置保存失败：" + error.getMessage());
            SlaveRuntimeLog.record("config_save_failed", "slave.json", error);
            return false;
        }
    }
}
