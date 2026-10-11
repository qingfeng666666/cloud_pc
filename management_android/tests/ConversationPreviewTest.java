package com.cloudpc.management;

public final class ConversationPreviewTest {
    public static void main(String[] args) {
        String longText = "assistant:\n" + "line\n".repeat(30);
        java.util.List<ConversationPreview.Block> blocks = ConversationPreview.blocks("user:\nhello\n\n" + longText);
        if (blocks.size() != 2) throw new AssertionError("message split");
        if (blocks.get(0).collapsible()) throw new AssertionError("short message");
        ConversationPreview.Block block = blocks.get(1);
        if (!block.collapsible() || !block.preview.endsWith("…")) throw new AssertionError("preview");
        if (!block.full.equals(longText)) throw new AssertionError("lossless full text");
        ConversationPreview.Block single = new ConversationPreview.Block("assistant:\n" + "字".repeat(1000));
        if (!single.collapsible()) throw new AssertionError("long single line");
        if (!single.id.equals(new ConversationPreview.Block(single.full + "append").id)) throw new AssertionError("stable expansion id");
        System.out.println("CONVERSATION_PREVIEW_OK");
    }
}
