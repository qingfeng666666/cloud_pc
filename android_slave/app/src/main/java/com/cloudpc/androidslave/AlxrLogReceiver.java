package com.cloudpc.androidslave;

import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

/** Receives ALXR's existing best-effort UDP diagnostics from the HoloLens. */
final class AlxrLogReceiver implements AutoCloseable {
    private static final String TAG = "CloudAlxrLog";
    private static final long ROTATE_BYTES = 16L * 1024 * 1024;
    private final File directory;
    private volatile DatagramSocket socket;

    AlxrLogReceiver(File sharedRoot) throws IOException {
        directory = new File(sharedRoot, "ar_diagnostics").getCanonicalFile();
        if (!directory.isDirectory() && !directory.mkdirs())
            throw new IOException("Cannot create ALXR diagnostic directory");
    }

    void start() throws IOException {
        socket = new DatagramSocket(9999, InetAddress.getByName("0.0.0.0"));
        Thread thread = new Thread(this::run, "alxr-log-udp");
        thread.setDaemon(true);
        thread.start();
        Log.i(TAG, "ALXR UDP diagnostic receiver ready on 9999");
    }

    private void run() {
        byte[] buffer = new byte[8192];
        while (socket != null && !socket.isClosed()) {
            DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
            try {
                socket.receive(packet);
                if (!HotspotPeer.contains(packet.getAddress())) continue;
                if (packet.getLength() == 0) continue;
                File current = new File(directory, "alxr-client.log");
                if (current.length() + packet.getLength() > ROTATE_BYTES) {
                    File previous = new File(directory, "alxr-client.previous.log");
                    Files.move(current.toPath(), previous.toPath(), StandardCopyOption.REPLACE_EXISTING);
                }
                try (FileOutputStream output = new FileOutputStream(current, true)) {
                    output.write(packet.getData(), packet.getOffset(), packet.getLength());
                }
            } catch (IOException error) {
                if (socket != null && !socket.isClosed()) Log.w(TAG, "UDP log receive", error);
            }
        }
    }

    @Override public void close() {
        if (socket != null) socket.close();
        socket = null;
    }
}
