package com.cloudpc.management;

import java.util.ArrayList;
import java.util.List;

/** Presentation only: never changes the provider's conversation text. */
final class ConversationPreview {
    static final class Block {
        final String id, full, preview;
        Block(String text) {
            full = text;
            id = Integer.toHexString(text.substring(0, Math.min(128, text.length())).hashCode());
            StringBuilder shortText = new StringBuilder();
            int lines = 0;
            for (String line : text.split("\n", -1)) {
                for (int offset = 0; offset < Math.max(1, line.length()); offset += 60) {
                    if (lines++ == 5) { preview = shortText.toString().trim() + "\n…"; return; }
                    shortText.append(line.substring(Math.min(offset, line.length()), Math.min(offset + 60, line.length()))).append('\n');
                }
            }
            preview = text;
        }
        boolean collapsible() { return !preview.equals(full); }
    }
    static List<Block> blocks(String text) {
        List<Block> result = new ArrayList<>();
        for (String part : text.split("(?m)(?=^(?:user|assistant|system|思考摘要|工具执行|工具结果|计划):\\n)"))
            if (!part.isEmpty()) result.add(new Block(part));
        return result;
    }
}
