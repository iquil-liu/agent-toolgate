"""Heading-aware Markdown splitting, bounded in Unicode characters."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Block:
    doc_id: str
    heading_path: tuple[str, ...]
    block_index: int
    text: str


def split_markdown(doc_id: str, text: str, max_chars: int = 600) -> list[Block]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    blocks = []
    headings = []
    lines = []
    fence = None

    def flush():
        content = "\n".join(lines).strip()
        lines.clear()
        pending = ""
        for paragraph in re.split(r"\n\s*\n", content):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if pending and len(pending) + 2 + len(paragraph) <= max_chars:
                pending += "\n\n" + paragraph
                continue
            if pending:
                blocks.append(Block(doc_id, tuple(h[1] for h in headings), len(blocks), pending))
                pending = ""
            while len(paragraph) > max_chars:
                blocks.append(Block(doc_id, tuple(h[1] for h in headings), len(blocks), paragraph[:max_chars]))
                paragraph = paragraph[max_chars:]
            pending = paragraph
        if pending:
            blocks.append(Block(doc_id, tuple(h[1] for h in headings), len(blocks), pending))

    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append(line)
            continue
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line) if fence is None else None
        if match:
            flush()
            level = len(match[1])
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, match[2]))
        else:
            lines.append(line)
    flush()
    return blocks
