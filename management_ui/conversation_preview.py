"""UI-only per-message preview; the API and durable history stay unchanged."""
import re


def compact_conversation(text, expanded=False, lines=5, width=60):
    if expanded: return text
    parts = re.split(r'(?m)(?=^(?:user|assistant|system|思考摘要|工具执行|工具结果|计划):\n)', text)
    output = []
    for part in parts:
        visual = [line[start:start + width] for line in part.split('\n')
                  for start in range(0, max(1, len(line)), width)]
        output.append('\n'.join(visual[:lines]).rstrip() + '\n…' if len(visual) > lines else part)
    return ''.join(part + ('\n\n' if part.endswith('…') else '') for part in output)
