package com.cloudpc.androidslave;

import android.util.Log;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
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

/** Narrow hotspot upload endpoint: one completed joints CSV or camera MP4 per connection. */
final class HandCsvReceiver implements AutoCloseable {
    private static final String TAG = "CloudHandCsv";
    static final int PORT = 9541;
    private static final long MAX_BYTES = 64L * 1024 * 1024;
    private static final byte[] CSV_MAGIC = "ARCSV001".getBytes(StandardCharsets.US_ASCII);
    private static final byte[] VIDEO_MAGIC = "ARVID001".getBytes(StandardCharsets.US_ASCII);
    private final File csvDestination;
    private final File videoDestination;
    private volatile ServerSocket listener;

    HandCsvReceiver(File sharedRoot) throws IOException {
        csvDestination = new File(sharedRoot, "ar_hand_joints").getCanonicalFile();
        videoDestination = new File(sharedRoot, "ar_hand_video").getCanonicalFile();
        if (!csvDestination.isDirectory() && !csvDestination.mkdirs())
            throw new IOException("Cannot create hand recording directory");
        if (!videoDestination.isDirectory() && !videoDestination.mkdirs())
            throw new IOException("Cannot create hand video directory");
    }

    void start() throws IOException {
        // This socket is only for the phone hotspot. Never expose CLDC 9527 here.
        listener = new ServerSocket(PORT, 4, InetAddress.getByName("0.0.0.0"));
        Thread thread = new Thread(() -> {
            while (!listener.isClosed()) {
                try {
                    Socket peer = listener.accept();
                    Thread transfer = new Thread(() -> receive(peer), "hand-csv-transfer");
                    transfer.setDaemon(true);
                    transfer.start();
                } catch (IOException error) {
                    if (!listener.isClosed()) Log.w(TAG, "accept", error);
                }
            }
        }, "hand-csv-accept");
        thread.setDaemon(true);
        thread.start();
        Log.i(TAG, "Hand CSV receiver ready on TCP " + PORT);
    }

    private void receive(Socket peer) {
        try (Socket socket = peer;
             DataInputStream input = new DataInputStream(new BufferedInputStream(socket.getInputStream()));
             DataOutputStream output = new DataOutputStream(new BufferedOutputStream(socket.getOutputStream()))) {
            socket.setSoTimeout(60_000);
            String source = socket.getInetAddress().getHostAddress();
            if (!HotspotPeer.contains(socket.getInetAddress()))
                throw new IOException("Source is not a client of this phone's hotspot");
            byte[] magic = new byte[CSV_MAGIC.length];
            input.readFully(magic);
            final boolean csv = Arrays.equals(magic, CSV_MAGIC);
            final boolean video = Arrays.equals(magic, VIDEO_MAGIC);
            if (!csv && !video) throw new IOException("Bad upload magic");
            int nameLength = input.readUnsignedShort();
            long size = input.readLong();
            if (nameLength < 20 || nameLength > 80 || size <= 0 || size > MAX_BYTES)
                throw new IOException("Invalid upload metadata");
            byte[] nameBytes = new byte[nameLength];
            input.readFully(nameBytes);
            String name = new String(nameBytes, StandardCharsets.US_ASCII);
            if (!(csv && name.matches("hand_joints_[0-9]+\\.csv")) &&
                    !(video && name.matches("hand_video_[0-9]+\\.mp4")))
                throw new IOException("Unsupported recording filename");
            File destination = csv ? csvDestination : videoDestination;
            File temporary = File.createTempFile(".hand-upload-", ".tmp", destination);
            try {
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                try (FileOutputStream file = new FileOutputStream(temporary)) {
                    byte[] buffer = new byte[32768];
                    long remaining = size;
                    while (remaining > 0) {
                        int count = input.read(buffer, 0, (int)Math.min(buffer.length, remaining));
                        if (count < 0) throw new IOException("Upload ended early");
                        file.write(buffer, 0, count);
                        digest.update(buffer, 0, count);
                        remaining -= count;
                    }
                    file.getFD().sync();
                }
                File target = new File(destination, name);
                Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING);
                output.write("OKAY".getBytes(StandardCharsets.US_ASCII));
                output.write(digest.digest());
                output.flush();
                Log.i(TAG, "Saved " + name + " bytes=" + size + " source=" + source);
            } finally {
                if (temporary.exists()) temporary.delete();
            }
        } catch (Exception error) {
            Log.w(TAG, "Upload failed", error);
        }
    }

    @Override public void close() {
        try { if (listener != null) listener.close(); } catch (IOException ignored) { }
    }
}
