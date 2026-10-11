package com.cloudpc.management;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.Menu;
import android.view.MenuItem;
import android.widget.*;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** UI only: talks to a local slave or local host; never across devices. */
public class MainActivity extends Activity {
    final Handler ui = new Handler(Looper.getMainLooper());
    final ExecutorService worker = Executors.newFixedThreadPool(2);
    JsonSettings config;
    LinearLayout root;
    TextView status, transcript, actionStatus;
    EditText input;
    Button send, stop;
    ScrollView scroll;
    ImageView image;
    android.graphics.Bitmap screenshotBitmap;
    int screenshotRotation;
    String fullTranscript = "";
    final java.util.Set<String> expandedBlocks = new java.util.HashSet<>();
    int page = -1, generation = 0;
    boolean active, polling, pending, first = true;
    String managementSession = UUID.randomUUID().toString().replace("-", "");
    boolean registered, registering, superseded;
    String revision = "";
    String llmProvider = "", llmSession = "", sessionCursor = "", lastError = "";
    LinearLayout sessionRows;
    LinearLayout slaveRows;
    JSONObject selectedSlave;
    boolean touchingHistory;
    long lastHistoryTouch;
    final Runnable timer = new Runnable() {
        public void run() { if (active) { if (!registered) activateManagement(); else poll(); ui.postDelayed(this, 1000); } }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) {
            managementSession = state.getString("management_session", managementSession);
            superseded = state.getBoolean("management_superseded", false);
        }
        RuntimeLog.init(this);
        RuntimeLog.write("activity_create", "package=" + getPackageName(), null);
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, failure) -> {
            RuntimeLog.write("uncaught", "thread=" + thread.getName(), failure);
            if (previous != null) previous.uncaughtException(thread, failure);
        });
        try {
            config = new JsonSettings(this, "management.json", new JSONObject()
                .put("on_host", false)
                .put("host_ip", "10.0.0.2")
                .put("last_llm_provider", "").put("last_llm_session", ""),
                getSharedPreferences("connection", MODE_PRIVATE));
        } catch (Exception error) {
            RuntimeLog.write("config_load_failed", "management.json", error);
            new AlertDialog.Builder(this).setTitle("配置读取失败（未覆盖文件）")
                .setMessage(error.toString()).setPositiveButton("关闭", (d,w) -> finish()).show();
            return;
        }
        getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        config.edit().remove("slave0").remove("slave1").apply();
        home();
    }
    @Override public void onResume() { super.onResume(); active = config != null; if (active) ui.post(timer); }
    @Override public void onPause() { active = false; ui.removeCallbacks(timer); super.onPause(); }
    @Override public void onSaveInstanceState(Bundle state) {
        state.putString("management_session", managementSession);
        state.putBoolean("management_superseded", superseded);
        super.onSaveInstanceState(state);
    }
    @Override public void onDestroy() {
        if (isFinishing() && registered) {
            try { request("/api/management/session/release", new JSONObject().put("session_id", managementSession), (r, e) -> {}); }
            catch (Exception ignored) {}
        }
        worker.shutdown(); super.onDestroy();
    }
    void activateManagement() {
        if (registered || registering || superseded) return;
        registering = true;
        final String requestedSession = managementSession;
        try {
            request("/api/management/session/activate", new JSONObject().put("session_id", managementSession)
                .put("host_ip", config.getString("host_ip", "10.0.0.2")), (result, error) -> {
                if (!requestedSession.equals(managementSession)) return;
                registering = false;
                if (result != null && "management_inactive".equals(result.optString("code"))) superseded = true;
                registered = error == null && result != null && result.optBoolean("ok");
                if (registered && page == 4) providers();
                if (registered && page == -1) loadSlaves();
                if (status != null && !registered) status.setText(superseded ? "本界面已被接替，请在设置中重新激活" : error != null ? error : result == null ? "等待主机注册" : result.optString("error"));
            });
        } catch (Exception ex) { registering = false; }
    }
    @Override public void onBackPressed() {
        if (page == 2) sessions(llmProvider);
        else if (page == 5) providers();
        else if (page >= 0) home(); else super.onBackPressed();
    }
    int dp(int value) { return (int)(value * getResources().getDisplayMetrics().density); }
    LinearLayout column() { LinearLayout v = new LinearLayout(this); v.setOrientation(1); v.setPadding(dp(12), dp(8), dp(12), dp(8)); return v; }
    TextView label(String text) { TextView v = new TextView(this); v.setText(text); v.setTextSize(16); return v; }
    Button button(String text, Runnable action) { Button v = new Button(this); v.setText(text); v.setOnClickListener(w -> action.run()); return v; }
    void reset(int value) {
        generation++; page = value; polling = false; first = true; revision = "";
        root = column(); setContentView(root);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars());
                android.graphics.Insets keyboard = insets.getInsets(android.view.WindowInsets.Type.ime());
                view.setPadding(dp(12) + bars.left, dp(8) + bars.top,
                    dp(12) + bars.right, dp(8) + Math.max(bars.bottom, keyboard.bottom));
            } else {
                view.setPadding(dp(12), dp(8) + insets.getSystemWindowInsetTop(),
                    dp(12), dp(8) + insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        root.requestApplyInsets();
    }
    void blank() { root.addView(new TextView(this), new LinearLayout.LayoutParams(-1, dp(64))); }
    void home() {
        reset(-1);
        TextView title = label("云电脑管理界面 " + BuildConfig.VERSION_NAME + " · 点击标题设置本机角色");
        title.setOnClickListener(v -> settings()); root.addView(title);
        root.addView(button("连接主机 / 设置主机 IP", this::settings));
        root.addView(button("添加从机", this::addSlave));
        root.addView(button("刷新从机列表", this::loadSlaves));
        slaveRows = column(); root.addView(slaveRows);
        root.addView(button("LLM 对话界面", () -> open(2)));
        root.addView(button("主机运行状态", () -> open(3)));
        status = label("连接主机后读取从机列表"); root.addView(status);
        Space space = new Space(this); root.addView(space, new LinearLayout.LayoutParams(-1, 0, 1));
        blank();
        if (registered) loadSlaves();
    }
    void loadSlaves() {
        if (page != -1 || !registered) return;
        final int epoch = generation;
        request("/api/management/slaves", new JSONObject(), (result, error) -> {
            if (epoch != generation || page != -1) return;
            if (error != null || result == null || !result.optBoolean("ok")) {
                status.setText(error != null ? error : result == null ? "无响应" : result.optString("error")); return;
            }
            slaveRows.removeAllViews();
            org.json.JSONArray list = result.optJSONArray("slaves");
            if (list != null) for (int i = 0; i < list.length(); i++) {
                final JSONObject item = list.optJSONObject(i);
                if (item == null) continue;
                LinearLayout row = new LinearLayout(this);
                Button show = button(item.optString("name") + " · " + item.optString("address")
                    + (item.optBoolean("connected") ? " · 在线" : " · 离线"), () -> {
                        selectedSlave = item; renderPage(0);
                    });
                row.addView(show, new LinearLayout.LayoutParams(0, -2, 1));
                row.addView(button("移除", () -> new AlertDialog.Builder(this).setTitle("移除从机")
                    .setMessage("从主机配置中移除 " + item.optString("address") + "？不会删除从机文件。")
                    .setPositiveButton("移除", (d,w) -> changeSlave("remove", item.optString("host"), item.optInt("port", 9527)))
                    .setNegativeButton("取消", null).show()));
                slaveRows.addView(row);
            }
            status.setText("从机列表来自主机 · 添加／移除自动保存到主机 host.json");
        });
    }
    void addSlave() {
        LinearLayout form = column();
        EditText host = new EditText(this); host.setHint("从机 IPv4 地址"); host.setSingleLine(true); form.addView(host);
        EditText port = new EditText(this); port.setInputType(InputType.TYPE_CLASS_NUMBER); port.setText("9527"); form.addView(port);
        new AlertDialog.Builder(this).setTitle("添加从机（保存到主机）").setView(form)
            .setPositiveButton("添加", (d,w) -> {
                try { changeSlave("add", host.getText().toString().trim(), Integer.parseInt(port.getText().toString())); }
                catch (Exception e) { status.setText("端口格式不正确"); }
            }).setNegativeButton("取消", null).show();
    }
    void changeSlave(String action, String host, int port) {
        if (!registered) { status.setText("请先连接主机"); return; }
        final int epoch = generation;
        try { request("/api/management/slaves", new JSONObject().put("action", action).put("host", host).put("port", port), (r,e) -> {
            if (epoch != generation || page != -1) return;
            if (e != null || r == null || !r.optBoolean("ok")) status.setText(e != null ? e : r == null ? "结果未确认，刷新列表核实" : r.optString("error"));
            else loadSlaves();
        }); } catch (Exception e) { status.setText(e.getMessage()); }
    }
    void open(int value) {
        if (value == 2) { providers(); return; }
        renderPage(value);
    }
    void providers() {
        reset(4);
        root.addView(button("返回首页", this::home));
        root.addView(label("LLM 对话界面 · 配置由用户预先在主机完成"));
        status = label("正在读取主机 LLM 配置"); root.addView(status);
        if (!registered) { activateManagement(); return; }
        final String previousProvider = config.getString("last_llm_provider", "");
        final String previousSession = config.getString("last_llm_session", "");
        if (!previousProvider.isEmpty() && !previousSession.isEmpty()) {
            root.addView(button("返回上次对话 · " + previousProvider, () -> {
                llmProvider = previousProvider;
                selectSession(previousSession);
            }));
        }
        request("/api/management/llm/providers", new JSONObject(), (result, error) -> {
            if (page != 4 || result == null) { if (page == 4) status.setText(error); return; }
            if (!result.optBoolean("ok")) { status.setText(result.optString("error")); return; }
            org.json.JSONArray items = result.optJSONArray("providers");
            if (items != null) for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.optJSONObject(i); if (item == null) continue;
                String id = item.optString("id");
                Button choice = button(id + (item.optBoolean("enabled") ? "" : "（主机未启用）"), () -> sessions(id));
                choice.setEnabled(item.optBoolean("enabled")); root.addView(choice);
            }
            status.setText("请选择 LLM"); blank();
        });
    }
    void sessions(String provider) {
        llmProvider = provider; llmSession = ""; sessionCursor = "";
        reset(5);
        root.addView(button("返回 LLM 列表", this::providers));
        root.addView(label(provider + " · 选择要继续的会话"));
        root.addView(button("新建会话", this::createSession));
        root.addView(button("刷新会话列表", () -> sessions(provider)));
        ScrollView list = new ScrollView(this); sessionRows = column(); list.addView(sessionRows);
        root.addView(list, new LinearLayout.LayoutParams(-1, 0, 1));
        status = label("正在读取会话"); root.addView(status);
        loadSessions("");
    }
    void loadSessions(String cursor) {
        try {
            JSONObject body = new JSONObject().put("provider", llmProvider);
            if (!cursor.isEmpty()) body.put("cursor", cursor);
            request("/api/management/llm/sessions", body, (result, error) -> {
                if (page != 5 || result == null) { if (page == 5) status.setText(error); return; }
                if (!result.optBoolean("ok")) { status.setText(result.optString("error")); return; }
                org.json.JSONArray rows = result.optJSONArray("sessions");
                if (rows != null) for (int i = 0; i < rows.length(); i++) {
                    JSONObject row = rows.optJSONObject(i); if (row == null) continue;
                    String sid = row.optString("id"), title = row.optString("title", sid);
                    Button entry = button(title + "\n" + sid + (row.optBoolean("busy") ? " · 工作中" : ""), () -> selectSession(sid));
                    entry.setMaxLines(4);
                    entry.setEllipsize(android.text.TextUtils.TruncateAt.END);
                    sessionRows.addView(entry);
                }
                sessionCursor = result.isNull("next_cursor") ? "" : result.optString("next_cursor");
                if (!sessionCursor.isEmpty()) {
                    final String next = sessionCursor;
                    Button more = button("更多会话", () -> loadSessions(next));
                    more.setOnClickListener(v -> { sessionRows.removeView(more); loadSessions(next); });
                    sessionRows.addView(more);
                }
                status.setText(sessionRows.getChildCount() == 0 ? "没有会话，可以新建" : "选择会话后继续原来的上下文");
            });
        } catch (Exception ex) { status.setText(ex.getMessage()); }
    }
    void createSession() {
        if (pending) return;
        pending = true;
        final int epoch = generation;
        try {
            JSONObject body = new JSONObject().put("provider", llmProvider)
                .put("request_id", "android-new-" + UUID.randomUUID());
            request("/api/management/llm/create", body, (result, error) -> {
                pending = false;
                if (page != 5 || epoch != generation) return;
                if (error != null || result == null || !result.optBoolean("ok")) {
                    status.setText(error != null ? error : result == null ? "结果未确认，未重发" : result.optString("error")); return;
                }
                selectSession(result.optString("session_id"));
            });
        } catch (Exception ex) { pending = false; status.setText(ex.getMessage()); }
    }
    void selectSession(String sid) {
        status.setText("正在打开会话");
        final int epoch = generation;
        final String provider = llmProvider;
        try {
            request("/api/management/llm/open", new JSONObject().put("provider", provider).put("session_id", sid), (result, error) -> {
                if ((page != 5 && page != 4) || epoch != generation) return;
                if (error != null || result == null || !result.optBoolean("ok")) {
                    status.setText(error != null ? error : result == null ? "未收到结果" : result.optString("error")); return;
                }
                llmProvider = provider; llmSession = sid;
                config.edit().putString("last_llm_provider", provider).putString("last_llm_session", sid).apply();
                renderPage(2);
            });
        } catch (Exception ex) { status.setText(ex.getMessage()); }
    }
    void renderPage(int value) {
        fullTranscript = ""; expandedBlocks.clear(); screenshotBitmap = null; screenshotRotation = 0;
        RuntimeLog.write("page_open", "page=" + value, null);
        reset(value);
        root.addView(button("返回首页", this::home));
        if (value == 2) {
            root.addView(button(llmProvider + " · 切换会话", () -> sessions(llmProvider)));
            root.setBackgroundColor(0xff101820);
            input = new EditText(this); input.setSingleLine(true); input.setHint("输入发送内容");
            input.setTextColor(0xff101820); input.setHintTextColor(0xff63737c);
            android.graphics.drawable.GradientDrawable inputBackground = new android.graphics.drawable.GradientDrawable();
            inputBackground.setColor(0xffffffff); inputBackground.setStroke(dp(2), 0xff40c4ff);
            inputBackground.setCornerRadius(dp(6)); input.setBackground(inputBackground);
            input.setPadding(dp(12), 0, dp(12), 0);
            TextView inputLabel = label("输入到 " + llmProvider + " 会话：");
            inputLabel.setTextColor(0xffffffff); root.addView(inputLabel);
            root.addView(input, new LinearLayout.LayoutParams(-1, dp(56)));
            LinearLayout row = new LinearLayout(this);
            send = button("SEND", () -> action("send")); stop = button("STOP", () -> action("stop")); stop.setEnabled(false);
            row.addView(send, new LinearLayout.LayoutParams(0, -2, 1)); row.addView(stop, new LinearLayout.LayoutParams(0, -2, 1)); root.addView(row);
            scroll = new ScrollView(this); transcript = label(""); transcript.setTextIsSelectable(true);
            scroll.setBackgroundColor(0xff202a32); scroll.setVerticalScrollBarEnabled(true);
            scroll.setScrollbarFadingEnabled(false);
            transcript.setTextColor(0xfff1f4f6); transcript.setTypeface(android.graphics.Typeface.MONOSPACE);
            transcript.setFocusable(false);
            transcript.setTextSize(14); transcript.setPadding(dp(12), dp(12), dp(12), dp(12));
            touchingHistory = false; lastHistoryTouch = 0;
            scroll.setOnTouchListener((v, event) -> {
                int action = event.getActionMasked();
                if (action == android.view.MotionEvent.ACTION_DOWN) touchingHistory = true;
                if (action == android.view.MotionEvent.ACTION_UP || action == android.view.MotionEvent.ACTION_CANCEL)
                    touchingHistory = false;
                lastHistoryTouch = android.os.SystemClock.uptimeMillis();
                return false;
            });
            scroll.addView(transcript); root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        } else if (value == 3) {
            scroll = new ScrollView(this); transcript = label("");
            transcript.setTypeface(android.graphics.Typeface.MONOSPACE);
            scroll.addView(transcript); root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        } else {
            root.addView(button("旋转截图 ↻ 90°", () -> { screenshotRotation = (screenshotRotation + 90) % 360; showScreenshot(); }));
            image = new ImageView(this); image.setScaleType(ImageView.ScaleType.FIT_CENTER);
            root.addView(image, new LinearLayout.LayoutParams(-1, 0, 1));
        }
        status = label("等待更新");
        status.setTextColor(android.graphics.Color.rgb(120, 220, 255));
        root.addView(status);
        if (value == 2) status.setVisibility(android.view.View.GONE);
        if (value == 2) {
            actionStatus = label("");
            actionStatus.setTextColor(0xffd5e1e8);
            actionStatus.setTextSize(13);
            actionStatus.setMaxLines(3);
            actionStatus.setPadding(0, dp(2), 0, dp(4));
            actionStatus.setVisibility(android.view.View.GONE);
            root.addView(actionStatus);
        } else blank();
        poll();
    }
    void settings() {
        LinearLayout form = column();
        form.addView(label("配置文件：" + config.file));
        form.addView(button("激活本管理界面", () -> {
            if (registering) return;
            managementSession = UUID.randomUUID().toString().replace("-", "");
            registered = registering = superseded = false;
            activateManagement();
        }));
        form.addView(label("管理界面所在设备"));
        Spinner role = new Spinner(this);
        role.setAdapter(new ArrayAdapter<String>(this, android.R.layout.simple_spinner_dropdown_item,
            new String[]{"从机设备（本机从机 Binder）", "主机设备（本机主机19527）"}));
        role.setSelection(config.getBoolean("on_host", false) ? 1 : 0); form.addView(role);
        form.addView(label("主机 IP（交由本机从机注册，端口19526）"));
        EditText hostIp = new EditText(this);
        hostIp.setSingleLine(true);
        hostIp.setText(config.getString("host_ip", "10.0.0.2")); form.addView(hostIp);
        new AlertDialog.Builder(this).setTitle("连接设置").setView(form).setPositiveButton("保存并连接主机", (d, w) -> {
            String target = hostIp.getText().toString().trim();
            if (!android.util.Patterns.IP_ADDRESS.matcher(target).matches() || target.contains(":")) {
                new AlertDialog.Builder(this).setMessage("请输入有效的 IPv4 地址").setPositiveButton("确定", null).show();
                return;
            }
            if (registered) {
                try { request("/api/management/session/release", new JSONObject().put("session_id", managementSession), (r, e) -> {}); }
                catch (Exception ignored) {}
            }
            JsonSettings.Editor editor = config.edit();
            editor.putBoolean("on_host", role.getSelectedItemPosition() == 1);
            editor.putString("host_ip", target);
            editor.remove("slave0").remove("slave1");
            editor.apply();
            managementSession = UUID.randomUUID().toString().replace("-", "");
            registered = registering = superseded = false;
            home();
            activateManagement();
        }).setNeutralButton("检测本机连接", (d, w) -> {
            config.edit().putBoolean("on_host", role.getSelectedItemPosition() == 1).apply();
            root.addView(label("正在检测本机程序…"));
            request("/health", null, (result, error) -> {
                String message = error != null ? error : result == null ? "无响应" : result.toString();
                new AlertDialog.Builder(this).setTitle("本机连接检测").setMessage(message).setPositiveButton("确定", null).show();
            });
        }).setNegativeButton("取消", null).show();
    }
    interface Completion { void done(JSONObject result, String error); }
    void request(String path, JSONObject body, Completion done) {
        final int epoch = generation;
        final boolean onHost = config.getBoolean("on_host", false);
        final String session = managementSession;
        final String trace = UUID.randomUUID().toString();
        final long started = android.os.SystemClock.elapsedRealtime();
        RuntimeLog.write("request_queued", "id=" + trace + " path=" + path + " role=" + (onHost ? "host" : "slave"), null);
        final int port = 19527;
        worker.execute(() -> {
            RuntimeLog.write("request_start", "id=" + trace, null);
            JSONObject result = null; String error = null;
            String stage = "连接本机127.0.0.1:" + port;
            try {
                if (!onHost) {
                    stage = "调用本机从机 Binder（请使用1.0.0或更新版本）";
                    String operation;
                    switch (path) {
                        case "/api/management/session/activate": operation = "management_activate"; break;
                        case "/api/management/session/release": operation = "management_release"; break;
                        case "/health": operation = "health"; break;
                        case "/api/management/current": operation = "current"; break;
                        case "/api/management/activity": operation = "activity"; break;
                        case "/api/management/current/send": operation = "send"; break;
                        case "/api/management/current/stop": operation = "stop"; break;
                        case "/api/management/llm/providers": operation = "llm_providers"; break;
                        case "/api/management/llm/sessions": operation = "llm_sessions"; break;
                        case "/api/management/llm/create": operation = "llm_create"; break;
                        case "/api/management/llm/open": operation = "llm_open"; break;
                        case "/api/management/llm/current": operation = "llm_current"; break;
                        case "/api/management/llm/send": operation = "llm_send"; break;
                        case "/api/management/llm/stop": operation = "llm_stop"; break;
                        case "/api/slaves": operation = "slaves"; break;
                        case "/api/management/slaves": operation = "slaves"; break;
                        case "/api/cloud/screen/capture": operation = "screenshot"; break;
                        default: throw new Exception("不支持的管理操作");
                    }
                    android.os.Bundle extras = new android.os.Bundle();
                    JSONObject message = body == null ? new JSONObject() : new JSONObject(body.toString());
                    message.put("_management_session", session);
                    extras.putString("body", message.toString());
                    RuntimeLog.write("binder_call", "id=" + trace + " method=" + operation +
                        " permission=" + checkSelfPermission("com.cloudpc.androidslave.permission.MANAGE_LOCAL") +
                        " provider_visible=" + (getPackageManager().resolveContentProvider(
                            "com.cloudpc.androidslave.management", 0) != null), null);
                    android.os.Bundle reply = getContentResolver().call(
                        android.net.Uri.parse("content://com.cloudpc.androidslave.management"),
                        operation, null, extras);
                    RuntimeLog.write("binder_return", "id=" + trace + " null=" + (reply == null), null);
                    if (reply == null) throw new Exception("本机从机未返回结果");
                    android.os.ParcelFileDescriptor fd = reply.getParcelable("response_fd");
                    String raw = reply.getString("result");
                    if (fd != null) {
                        try (InputStream input = new android.os.ParcelFileDescriptor.AutoCloseInputStream(fd);
                                ByteArrayOutputStream buffer = new ByteArrayOutputStream()) {
                            byte[] chunk = new byte[8192]; int count;
                            while ((count = input.read(chunk)) != -1) {
                                if (buffer.size() + count > 4 * 1024 * 1024)
                                    throw new Exception("本机响应过大");
                                buffer.write(chunk, 0, count);
                            }
                            raw = buffer.toString("UTF-8");
                        }
                    }
                    if (raw == null) throw new Exception("本机响应为空");
                    result = new JSONObject(raw);
                } else try (java.net.Socket socket = new java.net.Socket(java.net.Proxy.NO_PROXY)) {
                socket.connect(new java.net.InetSocketAddress("127.0.0.1", port), 3000);
                socket.setSoTimeout(35000); // Slave expires a pending request at 30s; read its explicit result.
                byte[] bytes = body == null ? new byte[0] : body.toString().getBytes(StandardCharsets.UTF_8);
                String headers = (body == null ? "GET " : "POST ") + path + " HTTP/1.1\r\nHost: 127.0.0.1:" + port
                    + "\r\nX-Management-Session: " + session
                    + "\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: " + bytes.length + "\r\n\r\n";
                java.io.OutputStream out = socket.getOutputStream();
                out.write(headers.getBytes(StandardCharsets.US_ASCII)); out.write(bytes); out.flush();
                stage = "等待本机程序返回结果（端口" + port + "）";
                InputStream stream = new java.io.BufferedInputStream(socket.getInputStream());
                String[] first = httpLine(stream).split(" ", 3);
                if (first.length < 2) throw new Exception("HTTP响应不完整");
                int code = Integer.parseInt(first[1]);
                int length = -1, total = 0;
                for (;;) {
                    String line = httpLine(stream); total += line.length();
                    if (total > 16384) throw new Exception("响应头过大");
                    if (line.isEmpty()) break;
                    if (line.toLowerCase(java.util.Locale.ROOT).startsWith("content-length:")) length = Integer.parseInt(line.substring(15).trim());
                }
                if (length < 0 || length > 4 * 1024 * 1024) throw new Exception("响应长度无效");
                byte[] response = new byte[length]; new java.io.DataInputStream(stream).readFully(response);
                result = new JSONObject(new String(response, StandardCharsets.UTF_8));
                if (code != 200) throw new Exception("本机 HTTP " + code + "：" + result.optString("error"));
                }
            } catch (Exception ex) {
                error = stage + "失败：" + ex.getClass().getSimpleName() + " " + ex.getMessage();
                RuntimeLog.write("request_exception", "id=" + trace + " stage=" + stage, ex);
            }
            RuntimeLog.write("request_end", "id=" + trace + " elapsed_ms=" +
                (android.os.SystemClock.elapsedRealtime() - started) + " transport_ok=" + (error == null) +
                " result_ok=" + (result != null && result.optBoolean("ok", true)), null);
            final JSONObject answer = result; final String failure = error;
            ui.post(() -> {
                if (!isDestroyed()) {
                    if (session.equals(managementSession) && answer != null && "management_inactive".equals(answer.optString("code"))) {
                        registered = false; superseded = true;
                        if (status != null) status.setText("本界面已被接替，请在设置中重新激活");
                    }
                    if (path.contains("/session/")) done.done(answer, failure);
                    else done.done(epoch == generation ? answer : null, epoch == generation ? failure : "页面已切换");
                }
            });
        });
    }
    String httpLine(InputStream stream) throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); int value;
        while ((value = stream.read()) >= 0 && value != '\n') {
            if (bytes.size() >= 8192) throw new Exception("响应头过大");
            if (value != '\r') bytes.write(value);
        }
        if (value < 0) throw new java.io.EOFException("响应中断");
        return bytes.toString("US-ASCII");
    }
    void poll() {
        if (page < 0 || page >= 4 || !active || polling || !registered) return;
        final int epoch = generation; final int targetPage = page;
        JSONObject body = null;
        try {
            if (page < 2) {
                String host = selectedSlave == null ? "" : selectedSlave.optString("host");
                if (host.isEmpty()) { status.setText("未配置该从机"); return; }
                body = new JSONObject().put("slave_host", host).put("slave_port", selectedSlave.optInt("port", 9527));
            } else if (page == 2) {
                body = new JSONObject().put("provider", llmProvider).put("session_id", llmSession);
            }
        } catch (Exception ex) { status.setText(ex.getMessage()); return; }
        polling = true;
        if (page != 2) status.setText("正在请求本机" + (config.getBoolean("on_host", false) ? "主机19527" : "从机 Binder") + "…");
        request(page == 3 ? "/api/management/activity" : page == 2 ? "/api/management/llm/current" : "/api/cloud/screen/capture", body, (result, error) -> {
            if (epoch != generation) return;
            polling = false;
            if (error != null || result == null || !result.optBoolean("ok")) {
                status.setVisibility(android.view.View.VISIBLE);
                status.setText((result != null ? result.optString("status_text", "连接中断") : "连接中断")
                    + " · 旧内容已过期：" + (error != null ? error : result == null ? "无响应" : result.optString("error")));
                if (targetPage == 2) {
                    stop.setEnabled(false); send.setEnabled(false);
                    String failure = status.getText().toString();
                    if (!failure.equals(lastError)) Toast.makeText(this, failure, Toast.LENGTH_LONG).show();
                    lastError = failure;
                } return;
            }
            if (targetPage == 3) {
                String next = Long.toString(result.optLong("revision"));
                if (!revision.equals(next)) {
                    StringBuilder text = new StringBuilder("设备连接\n");
                    org.json.JSONArray devices = result.optJSONArray("connections");
                    if (devices != null) for (int i = 0; i < devices.length(); i++) {
                        JSONObject item = devices.optJSONObject(i);
                        text.append(item.optString("address")).append("  ").append(item.optString("state")).append("\n");
                    }
                    text.append("\n最新运行记录（最多500条）\n");
                    org.json.JSONArray records = result.optJSONArray("records");
                    if (records != null) for (int i = records.length() - 1; i >= 0; i--) {
                        JSONObject item = records.optJSONObject(i);
                        String time = new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.ROOT)
                            .format(new java.util.Date((long)(item.optDouble("time") * 1000)));
                        text.append(time).append("  ").append(item.optString("ip")).append("  ")
                            .append(item.optString("action")).append("  ").append(item.optString("state")).append("\n");
                    }
                    int oldY = scroll.getScrollY(); transcript.setText(text.toString()); revision = next;
                    scroll.post(() -> { if (epoch == generation) scroll.scrollTo(0, oldY); });
                }
                status.setText("每秒更新 · 最新记录在上");
            } else if (targetPage == 2) {
                lastError = "";
                String next = result.optString("session_id") + ":" + result.optString("revision");
                boolean atBottom = scroll.getScrollY() + scroll.getHeight()
                    >= transcript.getHeight() + scroll.getPaddingBottom() - dp(8);
                if (!revision.equals(next) && !touchingHistory &&
                        android.os.SystemClock.uptimeMillis() - lastHistoryTouch > 1000 &&
                        (first || atBottom)) {
                    String text = result.optString("text");
                    fullTranscript = text;
                    showConversation();
                    first = false; revision = next;
                    final ScrollView targetScroll = scroll;
                    final TextView targetTranscript = transcript;
                    targetScroll.getViewTreeObserver().addOnGlobalLayoutListener(
                        new android.view.ViewTreeObserver.OnGlobalLayoutListener() {
                            @Override public void onGlobalLayout() {
                                if (targetScroll.getViewTreeObserver().isAlive())
                                    targetScroll.getViewTreeObserver().removeOnGlobalLayoutListener(this);
                                if (epoch == generation && !touchingHistory &&
                                        android.os.SystemClock.uptimeMillis() - lastHistoryTouch > 1000)
                                    targetScroll.scrollTo(0, Math.max(0, targetTranscript.getHeight() - targetScroll.getHeight()));
                            }
                        });
                }
                stop.setEnabled(result.optBoolean("busy") && !result.optBoolean("stopped") && !pending);
                send.setEnabled(!pending);
                status.setVisibility(android.view.View.VISIBLE);
                status.setText(result.optString("status_text", result.optBoolean("busy") ? "Working（" + result.optLong("working_seconds", 0) + "s）" : "空闲"));
            } else {
                try {
                    byte[] bytes = Base64.decode(result.getString("image_base64"), Base64.DEFAULT);
                    android.graphics.Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                    if (bitmap == null) throw new Exception("图片无法解码");
                    screenshotBitmap = bitmap; showScreenshot(); status.setText("截图已更新 · 每秒请求 · 旋转 " + screenshotRotation + "°");
                } catch (Exception ex) { status.setText("截图无效：" + ex.getMessage()); }
            }
        });
    }
    void showConversation() {
        android.text.SpannableStringBuilder content = new android.text.SpannableStringBuilder();
        for (ConversationPreview.Block block : ConversationPreview.blocks(fullTranscript)) {
            boolean expanded = expandedBlocks.contains(block.id);
            content.append(expanded ? block.full : block.preview);
            if (block.collapsible()) {
                content.append("\n"); int start = content.length();
                content.append(expanded ? "[收起]" : "[展开]");
                content.setSpan(new android.text.style.ClickableSpan() {
                    @Override public void onClick(android.view.View view) {
                        int y = scroll.getScrollY();
                        if (!expandedBlocks.add(block.id)) expandedBlocks.remove(block.id);
                        lastHistoryTouch = android.os.SystemClock.uptimeMillis();
                        showConversation(); scroll.post(() -> scroll.scrollTo(0, y));
                    }
                }, start, content.length(), android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
            content.append("\n\n");
        }
        transcript.setText(content);
        transcript.setMovementMethod(android.text.method.LinkMovementMethod.getInstance());
    }
    void showScreenshot() {
        if (screenshotBitmap == null || image == null) return;
        android.graphics.Matrix matrix = new android.graphics.Matrix(); matrix.postRotate(screenshotRotation);
        image.setImageBitmap(screenshotRotation == 0 ? screenshotBitmap : android.graphics.Bitmap.createBitmap(
            screenshotBitmap, 0, 0, screenshotBitmap.getWidth(), screenshotBitmap.getHeight(), matrix, true));
    }
    void action(String kind) {
        if (pending) return;
        final String text = input.getText().toString(); final int epoch = generation;
        if (kind.equals("send") && (text.trim().isEmpty() || text.getBytes(StandardCharsets.UTF_8).length > 16384 || text.indexOf('\0') >= 0)) {
            Toast.makeText(this, "请输入非空文字，最多16KB", Toast.LENGTH_SHORT).show(); return;
        }
        try {
            JSONObject body = new JSONObject().put("request_id", "android-ui-" + UUID.randomUUID().toString());
            body.put("provider", llmProvider).put("session_id", llmSession);
            if (kind.equals("send")) body.put("text", text);
            pending = true; send.setEnabled(false); stop.setEnabled(false);
            actionStatus.setVisibility(android.view.View.VISIBLE);
            actionStatus.setText("提交中，不会自动重发");
            request("/api/management/llm/" + kind, body, (result, error) -> {
                pending = false;
                if (epoch != generation || page != 2) return;
                send.setEnabled(true);
                if (error != null || result == null) {
                    actionStatus.setText("结果未确认，未重发：" + (error != null ? error : "未收到响应")); return;
                }
                actionStatus.setText("#" + result.optString("number", "?") + " " + result.optString("message", result.optString("error")));
                if (kind.equals("send") && result.optBoolean("ok") && input.getText().toString().equals(text)) input.setText("");
            });
        } catch (Exception ex) {
            pending = false;
            actionStatus.setVisibility(android.view.View.VISIBLE);
            actionStatus.setText(ex.getMessage());
            send.setEnabled(true);
        }
    }
}
