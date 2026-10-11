package com.cloudpc.androidslave;

import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.List;
import java.util.Map;
import java.util.ArrayList;
import java.util.UUID;
import java.util.Enumeration;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/** A deliberately narrow CLDC-to-Device-Portal bridge for a hotspot-connected HoloLens. */
final class HoloLensPortalRelay implements AutoCloseable {
    private static final long MAX_PACKAGE_BYTES = 64L * 1024 * 1024;
    private static final int MAX_CHUNK_BYTES = 512 * 1024;
    private final File cacheDir;
    private final ExecutorService installer = Executors.newSingleThreadExecutor();
    private Upload upload;
    private InstallJob job;

    HoloLensPortalRelay(File cacheDir) throws IOException {
        this.cacheDir = new File(cacheDir, "hololens-portal");
        if (!this.cacheDir.isDirectory() && !this.cacheDir.mkdirs())
            throw new IOException("Cannot create portal staging directory");
    }

    synchronized JSONObject handle(JSONObject request) throws Exception {
        String action = request.optString("action", "");
        switch (action) {
            case "portal_probe": return probe(request);
            case "portal_stage_begin": return stageBegin(request);
            case "portal_stage_chunk": return stageChunk(request);
            case "portal_stage_install": return stageInstall(request);
            case "portal_status": return status(request);
            case "portal_inspect": return inspect(request);
            case "portal_file_list": return fileList(request);
            case "portal_file_chunk": return fileChunk(request);
            case "phone_network": return phoneNetwork();
            default: throw new IOException("Unsupported Android bridge action");
        }
    }

    private static JSONObject phoneNetwork() throws Exception {
        JSONArray addresses = new JSONArray();
        Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        while (interfaces.hasMoreElements()) {
            NetworkInterface network = interfaces.nextElement();
            if (!network.isUp()) continue;
            Enumeration<InetAddress> ips = network.getInetAddresses();
            while (ips.hasMoreElements()) {
                InetAddress ip = ips.nextElement();
                if (ip instanceof Inet4Address && !ip.isLoopbackAddress())
                    addresses.put(new JSONObject().put("interface", network.getName())
                        .put("ipv4", ip.getHostAddress()));
            }
        }
        return new JSONObject().put("ok", true).put("addresses", addresses);
    }

    private JSONObject probe(JSONObject request) throws Exception {
        String ip = checkedIp(request.getString("device_ip"));
        HttpsURLConnection connection = connect(ip, "/api/os/info", null);
        try {
            int http = connection.getResponseCode();
            X509Certificate certificate = (X509Certificate) connection.getServerCertificates()[0];
            return new JSONObject().put("ok", true).put("device_ip", ip)
                    .put("http_status", http)
                    .put("cert_sha256", hex(sha256(certificate.getEncoded())));
        } finally { connection.disconnect(); }
    }

    private JSONObject stageBegin(JSONObject request) throws Exception {
        if (job != null && ("queued".equals(job.state) || "installing".equals(job.state) ||
                "submitted".equals(job.state)))
            throw new IOException("A portal installation is already running");
        long size = request.getLong("size");
        String digest = checkedDigest(request.getString("sha256"));
        String packageName = request.getString("package_name");
        if (size < 1 || size > MAX_PACKAGE_BYTES ||
                !packageName.matches("[A-Za-z0-9_.-]{1,120}\\.(?i:msix|appx)"))
            throw new IOException("Invalid package name or size");
        if (upload != null) upload.file.delete();
        upload = new Upload(UUID.randomUUID().toString(),
                File.createTempFile("alxr-stage-", ".package", cacheDir),
                packageName, size, digest);
        return new JSONObject().put("ok", true).put("transfer_id", upload.id)
                .put("max_chunk_bytes", MAX_CHUNK_BYTES);
    }

    private JSONObject stageChunk(JSONObject request) throws Exception {
        Upload current = requireUpload(request);
        int index = request.getInt("chunk_index");
        if (index != current.nextIndex) throw new IOException("Unexpected chunk index");
        byte[] bytes = Base64.decode(request.getString("data_base64"), Base64.DEFAULT);
        if (bytes.length == 0 || bytes.length > MAX_CHUNK_BYTES ||
                current.written + bytes.length > current.size)
            throw new IOException("Invalid package chunk size");
        try (FileOutputStream output = new FileOutputStream(current.file, true)) {
            output.write(bytes);
        }
        current.digest.update(bytes);
        current.written += bytes.length;
        current.nextIndex++;
        return new JSONObject().put("ok", true).put("transfer_id", current.id)
                .put("next_chunk", current.nextIndex).put("received_bytes", current.written);
    }

    private JSONObject stageInstall(JSONObject request) throws Exception {
        Upload current = requireUpload(request);
        String ip = checkedIp(request.getString("device_ip"));
        String certificate = checkedDigest(request.getString("cert_sha256"));
        String user = request.getString("username");
        String password = request.getString("password");
        if (user.isEmpty() || user.contains(":") || user.length() > 128 || password.isEmpty())
            throw new IOException("Invalid Device Portal credentials");
        if (current.written != current.size || current.file.length() != current.size ||
                !current.sha256.equals(hex(current.digest.digest()))) {
            current.file.delete();
            upload = null;
            throw new IOException("Package size or SHA-256 mismatch");
        }
        upload = null;
        InstallJob next = new InstallJob(UUID.randomUUID().toString(), current.packageName);
        job = next;
        installer.execute(() -> install(next, current.file, ip, certificate, user, password));
        return next.json();
    }

    private JSONObject status(JSONObject request) throws Exception {
        if (job == null || !job.id.equals(request.getString("job_id")))
            throw new IOException("Unknown portal job");
        return job.json();
    }

    private Upload requireUpload(JSONObject request) throws Exception {
        if (upload == null || !upload.id.equals(request.getString("transfer_id")))
            throw new IOException("Unknown portal transfer");
        return upload;
    }

    private void install(InstallJob result, File packageFile, String ip, String certificate,
                         String user, String password) {
        result.state = "installing";
        try {
            String auth = "Basic " + Base64.encodeToString(
                    (user + ":" + password).getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
            HttpsURLConnection info = connect(ip, "/api/os/info", certificate);
            info.setRequestProperty("Authorization", auth);
            String csrf = "";
            List<String> cookies = new ArrayList<>();
            try {
                int http = info.getResponseCode();
                if (http != 200) throw new IOException("Device Portal auth HTTP " + http);
                for (Map.Entry<String, List<String>> header : info.getHeaderFields().entrySet()) {
                    if (header.getKey() == null ||
                            !header.getKey().equalsIgnoreCase("Set-Cookie")) continue;
                    for (String cookie : header.getValue()) {
                        String pair = cookie.split(";", 2)[0];
                        cookies.add(pair);
                        if (pair.regionMatches(true, 0, "CSRF-Token=", 0, 11))
                            csrf = pair.substring(11);
                    }
                }
            } finally { info.disconnect(); }

            String path = "/api/app/packagemanager/package?package=" +
                    URLEncoder.encode(result.packageName, "UTF-8");
            HttpsURLConnection post = connect(ip, path, certificate);
            try {
                post.setRequestMethod("POST");
                post.setRequestProperty("Authorization", auth);
                if (!csrf.isEmpty()) post.setRequestProperty("X-CSRF-Token", csrf);
                if (!cookies.isEmpty()) post.setRequestProperty("Cookie", String.join("; ", cookies));
                String boundary = "alxr" + UUID.randomUUID().toString().replace("-", "");
                post.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
                byte[] prefix = ("--" + boundary + "\r\n" +
                        "Content-Disposition: form-data; name=\"package\"; filename=\"" +
                        result.packageName + "\"\r\n" +
                        "Content-Type: application/octet-stream\r\n\r\n")
                        .getBytes(StandardCharsets.UTF_8);
                byte[] suffix = ("\r\n--" + boundary + "--\r\n")
                        .getBytes(StandardCharsets.UTF_8);
                post.setDoOutput(true);
                post.setFixedLengthStreamingMode((long) prefix.length + packageFile.length() + suffix.length);
                try (OutputStream output = post.getOutputStream();
                     FileInputStream input = new FileInputStream(packageFile)) {
                    output.write(prefix);
                    byte[] buffer = new byte[65536];
                    int read;
                    while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
                    output.write(suffix);
                }
                result.httpStatus = post.getResponseCode();
                if (result.httpStatus < 200 || result.httpStatus >= 300)
                    throw new IOException("Device Portal install HTTP " + result.httpStatus +
                            ": " + readExcerpt(post.getErrorStream()));
            } finally { post.disconnect(); }
            result.state = "submitted";
            String expected = result.packageName.replaceFirst("^.*_(\\d+\\.\\d+\\.\\d+\\.\\d+)_.*$", "$1");
            for (int attempt = 0; attempt < 20; attempt++) {
                HttpsURLConnection packages = connect(ip,
                        "/api/app/packagemanager/packages", certificate);
                packages.setRequestProperty("Authorization", auth);
                try {
                    if (packages.getResponseCode() == 200) {
                        JSONObject body = new JSONObject(readExcerpt(packages.getInputStream(), 1024 * 1024));
                        JSONArray installed = body.optJSONArray("InstalledPackages");
                        if (installed != null) for (int i = 0; i < installed.length(); i++) {
                            JSONObject item = installed.optJSONObject(i);
                            if (item == null || !isAlxrPackage(item)) continue;
                            result.installedVersion = packageVersion(item);
                            if (expected.equals(result.installedVersion)) {
                                result.state = "verified";
                                return;
                            }
                        }
                    }
                } finally { packages.disconnect(); }
                Thread.sleep(2000);
            }
            result.state = "submitted_unverified";
        } catch (Exception error) {
            result.error = error.getMessage() == null ? error.getClass().getSimpleName() :
                    error.getMessage().substring(0, Math.min(300, error.getMessage().length()));
            // A successful POST may have installed the package even if the
            // verification GET later fails. Never report that as definite failure.
            result.state = "submitted".equals(result.state) ?
                    "submitted_unverified" : "failed";
        } finally { packageFile.delete(); }
    }

    private JSONObject inspect(JSONObject request) throws Exception {
        String ip = checkedIp(request.getString("device_ip"));
        String certificate = checkedDigest(request.getString("cert_sha256"));
        String user = request.getString("username");
        String password = request.getString("password");
        if (user.isEmpty() || password.isEmpty())
            throw new IOException("Device Portal credentials are required");
        String auth = "Basic " + Base64.encodeToString(
                (user + ":" + password).getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
        JSONObject answer = new JSONObject().put("ok", true);

        HttpsURLConnection state = connect(ip, "/api/app/packagemanager/state", certificate);
        try {
            state.setRequestProperty("Authorization", auth);
            int status = state.getResponseCode();
            answer.put("deployment_state_http", status);
            if (status == 200)
                answer.put("deployment_state", readExcerpt(state.getInputStream(), 4096));
            else if (status >= 400)
                answer.put("deployment_state_error", readExcerpt(state.getErrorStream()));
        } finally { state.disconnect(); }

        HttpsURLConnection packages = connect(ip, "/api/app/packagemanager/packages", certificate);
        try {
            packages.setRequestProperty("Authorization", auth);
            int status = packages.getResponseCode();
            answer.put("packages_http", status);
            if (status == 200) {
                JSONObject body = new JSONObject(readExcerpt(packages.getInputStream(), 4 * 1024 * 1024));
                JSONArray installed = body.optJSONArray("InstalledPackages");
                answer.put("package_response_keys", body.names());
                answer.put("installed_count", installed == null ? -1 : installed.length());
                JSONArray samples = new JSONArray();
                if (installed != null) for (int i = 0; i < installed.length(); i++) {
                    JSONObject item = installed.optJSONObject(i);
                    if (item == null) continue;
                    if (isAlxrPackage(item) || samples.length() < 3) {
                        samples.put(new JSONObject()
                                .put("name", item.optString("Name", ""))
                                .put("full_name", item.optString("PackageFullName", ""))
                                .put("version", packageVersion(item)));
                    }
                    if (isAlxrPackage(item)) {
                        answer.put("alxr_name", item.optString("Name", ""));
                        answer.put("alxr_package_full_name", item.optString("PackageFullName", ""));
                        answer.put("alxr_version", packageVersion(item));
                    }
                }
                answer.put("package_samples", samples);
            } else if (status >= 400) {
                answer.put("packages_error", readExcerpt(packages.getErrorStream()));
            }
        } finally { packages.disconnect(); }
        return answer;
    }

    private static String packageVersion(JSONObject item) {
        JSONObject version = item.optJSONObject("Version");
        if (version != null) return version.optInt("Major", 0) + "." +
                version.optInt("Minor", 0) + "." + version.optInt("Build", 0) + "." +
                version.optInt("Revision", 0);
        return item.optString("Version", "");
    }

    private String packageForScope(String ip, String certificate, String auth,
                                   String scope) throws Exception {
        String prefix;
        if ("alxr".equals(scope)) prefix = "ALVR.alxr-client-diag_";
        else if ("slave".equals(scope))
            prefix = "791a751b-c4aa-48c2-a310-4563991d8c88_";
        else throw new IOException("Unsupported recording scope");
        HttpsURLConnection connection = connect(ip, "/api/app/packagemanager/packages", certificate);
        connection.setRequestProperty("Authorization", auth);
        try {
            if (connection.getResponseCode() != 200)
                throw new IOException("Package lookup HTTP " + connection.getResponseCode());
            JSONObject result = new JSONObject(readExcerpt(connection.getInputStream(), 4 * 1024 * 1024));
            JSONArray packages = result.optJSONArray("InstalledPackages");
            if (packages != null) for (int i=0;i<packages.length();i++) {
                JSONObject item=packages.optJSONObject(i);
                if (item == null) continue;
                String full=item.optString("PackageFullName", "");
                if (full.startsWith(prefix)) return full;
            }
            throw new IOException("Recording package not installed: " + scope);
        } finally { connection.disconnect(); }
    }

    private static String recordingPath(String scope) throws IOException {
        if ("alxr".equals(scope)) return "LocalState";
        if ("slave".equals(scope)) return "LocalState\\shared\\ar_hand_joints";
        throw new IOException("Unsupported recording scope");
    }

    private static String recordingAuth(JSONObject request) throws Exception {
        String user=request.getString("username");
        String password=request.getString("password");
        if (user.isEmpty() || user.contains(":") || password.isEmpty())
            throw new IOException("Invalid Device Portal credentials");
        return "Basic " + Base64.encodeToString(
            (user+":"+password).getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
    }

    private static String filesUrl(String packageName, String path, String filename)
            throws Exception {
        String query="knownfolderid=LocalAppData&packagefullname="+
            URLEncoder.encode(packageName,"UTF-8")+"&path="+URLEncoder.encode(path,"UTF-8");
        if (filename != null) query += "&filename="+URLEncoder.encode(filename,"UTF-8");
        return "/api/filesystem/apps/"+(filename == null ? "files" : "file")+"?"+query;
    }

    private JSONObject fileList(JSONObject request) throws Exception {
        String ip=checkedIp(request.getString("device_ip"));
        String certificate=checkedDigest(request.getString("cert_sha256"));
        String auth=recordingAuth(request);
        String scope=request.getString("scope");
        String packageName=packageForScope(ip,certificate,auth,scope);
        HttpsURLConnection connection=connect(ip,
            filesUrl(packageName,recordingPath(scope),null),certificate);
        connection.setRequestProperty("Authorization",auth);
        try {
            int http=connection.getResponseCode();
            if (http!=200) throw new IOException("File list HTTP "+http+": "+
                readExcerpt(connection.getErrorStream()));
            JSONObject result=new JSONObject(readExcerpt(connection.getInputStream(),4*1024*1024));
            return new JSONObject().put("ok",true).put("package_full_name",packageName)
                .put("scope",scope).put("items",result.optJSONArray("Items"));
        } finally { connection.disconnect(); }
    }

    private JSONObject fileChunk(JSONObject request) throws Exception {
        String ip=checkedIp(request.getString("device_ip"));
        String certificate=checkedDigest(request.getString("cert_sha256"));
        String auth=recordingAuth(request);
        String scope=request.getString("scope");
        String filename=request.getString("filename");
        if (!filename.matches("hand_joints_[0-9]+\\.csv"))
            throw new IOException("Unsupported recording filename");
        long offset=request.getLong("offset");
        int count=request.getInt("count");
        if (offset<0 || offset>64L*1024*1024 || count<1 || count>256*1024)
            throw new IOException("Invalid recording range");
        String packageName=packageForScope(ip,certificate,auth,scope);
        HttpsURLConnection connection=connect(ip,
            filesUrl(packageName,recordingPath(scope),filename),certificate);
        connection.setRequestProperty("Authorization",auth);
        try {
            int http=connection.getResponseCode();
            if (http!=200) throw new IOException("File download HTTP "+http+": "+
                readExcerpt(connection.getErrorStream()));
            try (InputStream input=connection.getInputStream()) {
                byte[] buffer=new byte[8192];
                long remaining=offset;
                while (remaining>0) {
                    int read=input.read(buffer,0,(int)Math.min(buffer.length,remaining));
                    if (read<0) throw new IOException("Recording shorter than requested offset");
                    remaining-=read;
                }
                ByteArrayOutputStream data=new ByteArrayOutputStream(count);
                while (data.size()<count) {
                    int read=input.read(buffer,0,Math.min(buffer.length,count-data.size()));
                    if (read<0) break;
                    data.write(buffer,0,read);
                }
                return new JSONObject().put("ok",true).put("filename",filename)
                    .put("offset",offset).put("size",data.size())
                    .put("data_base64",Base64.encodeToString(data.toByteArray(),Base64.NO_WRAP));
            }
        } finally { connection.disconnect(); }
    }

    private static boolean isAlxrPackage(JSONObject item) {
        return (item.optString("Name", "") + " " +
                item.optString("PackageFullName", "") + " " +
                item.optString("DisplayName", "")).toLowerCase().contains("alxr");
    }

    private static HttpsURLConnection connect(String ip, String path, String pinnedSha256)
            throws Exception {
        TrustManager[] managers = {new X509TrustManager() {
            @Override public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            @Override public void checkClientTrusted(X509Certificate[] chain, String authType) {}
            @Override public void checkServerTrusted(X509Certificate[] chain, String authType)
                    throws CertificateException {
                if (chain == null || chain.length == 0) throw new CertificateException("No certificate");
                if (pinnedSha256 == null) return; // Fingerprint discovery only; never send credentials.
                try {
                    if (!pinnedSha256.equals(hex(sha256(chain[0].getEncoded()))))
                        throw new CertificateException("Device Portal certificate changed");
                } catch (CertificateException error) { throw error; }
                catch (Exception error) { throw new CertificateException(error); }
            }
        }};
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(null, managers, null);
        HttpsURLConnection connection = (HttpsURLConnection)
                new URL("https://" + ip + path).openConnection();
        HostnameVerifier hostname = (host, session) -> host.equals(ip);
        connection.setHostnameVerifier(hostname);
        connection.setSSLSocketFactory(context.getSocketFactory());
        connection.setConnectTimeout(8000);
        connection.setReadTimeout(180000);
        connection.setUseCaches(false);
        return connection;
    }

    private static String checkedIp(String value) throws IOException {
        // This is not a general-purpose private-network HTTP proxy. The address
        // must be a client of the phone's currently active wlan0 hotspot subnet.
        if (!value.matches("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}") ||
                !HotspotPeer.contains(InetAddress.getByName(value)))
            throw new IOException("Device IP is outside the configured hotspot subnet");
        return value;
    }

    private static String checkedDigest(String value) throws IOException {
        String digest = value.toLowerCase();
        if (!digest.matches("[0-9a-f]{64}")) throw new IOException("Invalid SHA-256 digest");
        return digest;
    }

    private static byte[] sha256(byte[] bytes) throws Exception {
        return MessageDigest.getInstance("SHA-256").digest(bytes);
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte item : bytes) result.append(String.format("%02x", item & 255));
        return result.toString();
    }

    private static String readExcerpt(InputStream input) throws IOException {
        return readExcerpt(input, 2048);
    }

    private static String readExcerpt(InputStream input, int maximum) throws IOException {
        if (input == null) return "";
        try (InputStream stream = input) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] bytes = new byte[Math.min(8192, maximum)];
            int count;
            while (output.size() < maximum &&
                    (count = stream.read(bytes, 0, Math.min(bytes.length,
                            maximum - output.size()))) != -1) output.write(bytes, 0, count);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    @Override public synchronized void close() {
        installer.shutdownNow();
        if (upload != null) { upload.file.delete(); upload = null; }
    }

    private static final class Upload {
        final String id;
        final File file;
        final String packageName;
        final long size;
        final String sha256;
        final MessageDigest digest;
        long written;
        int nextIndex;
        Upload(String id, File file, String packageName, long size, String sha256) throws Exception {
            this.id = id; this.file = file; this.packageName = packageName;
            this.size = size; this.sha256 = sha256;
            this.digest = MessageDigest.getInstance("SHA-256");
        }
    }

    private static final class InstallJob {
        final String id;
        final String packageName;
        volatile String state = "queued";
        volatile String error = "";
        volatile String installedVersion = "";
        volatile int httpStatus;
        InstallJob(String id, String packageName) { this.id = id; this.packageName = packageName; }
        JSONObject json() throws Exception {
            return new JSONObject().put("ok", !"failed".equals(state))
                    .put("job_id", id).put("state", state)
                    .put("package_name", packageName).put("http_status", httpStatus)
                    .put("installed_version", installedVersion).put("error", error);
        }
    }
}
