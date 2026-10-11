package com.cloudpc.androidslave;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.util.Enumeration;

/** Limit narrow HoloLens endpoints to clients of this phone's current hotspot. */
final class HotspotPeer {
    private HotspotPeer() { }

    static boolean contains(InetAddress peer) {
        if (!(peer instanceof Inet4Address)) return false;
        try {
            byte[] remote = peer.getAddress();
            Enumeration<NetworkInterface> networks = NetworkInterface.getNetworkInterfaces();
            while (networks.hasMoreElements()) {
                NetworkInterface wifi = networks.nextElement();
                if (!wifi.isUp() || !(wifi.getName().startsWith("wlan") ||
                        wifi.getName().startsWith("ap"))) continue;
                for (InterfaceAddress binding : wifi.getInterfaceAddresses()) {
                    InetAddress local = binding.getAddress();
                    if (!(local instanceof Inet4Address) || local.equals(peer)) continue;
                    int prefix = binding.getNetworkPrefixLength();
                    if (prefix < 24 || prefix > 30) continue;
                    byte[] phone = local.getAddress();
                    boolean matches = true;
                    for (int i = 0; i < 4; i++) {
                        int bits = Math.max(0, Math.min(8, prefix - i * 8));
                        int mask = bits == 0 ? 0 : (0xff << (8 - bits)) & 0xff;
                        if ((phone[i] & mask) != (remote[i] & mask)) matches = false;
                    }
                    if (matches) return true;
                }
            }
        } catch (Exception ignored) { }
        return false;
    }
}
