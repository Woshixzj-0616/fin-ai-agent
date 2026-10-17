"""文档语义检索：切块 + BM25（中文字符 n-gram / 英文词）+ 可选 embedding 钩子。

定位
----
- **只做检索，不做裁决**：返回 page/snippet 供人工或工具定位，不能单独定罪。
- 无重依赖：默认纯 Python BM25；若安装了 `sentence_transformers` 可走 embedding 通道。
- 中文用字符 unigram+bigram，英文用小写词——比 `query in text` 子串匹配覆盖同义/分词差异。

证据链约束
----------
命中片段必须能回到 `page` + 原文位置；调用方仍应把数值结论交给
`compare_claim` / bbox 证据，而不是检索命中。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
_SPLIT_RE = re.compile(r"[。！？；\n\r]+")

EMBEDDING_HOOK = None  # 可选：callable(list[str]) -> list[list[float]]


def set_embedding_hook(fn) -> None:
    """注入外部 embedding（如 sentence-transformers）；默认 None 走 BM25。"""
    global EMBEDDING_HOOK
    EMBEDDING_HOOK = fn


def tokenize(text: str) -> list[str]:
    """中文 char unigram+bigram + 英文/数字小写词。"""
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0)
        if re.match(r"[A-Za-z0-9]", tok):
            tokens.append(tok.lower())
        else:
            tokens.append(tok)
            # 相邻汉字再并一个 bigram，提升「毛利率」「归母」类切分稳定性
    # bigram 在整串汉字窗口上生成
    hans = re.findall(r"[\u4e00-\u9fff]", text or "")
    for i in range(len(hans) - 1):
        tokens.append(hans[i] + hans[i + 1])
    return tokens


def chunk_pages(pages: list[str], *, max_chars: int = 400) -> list[dict]:
    """按句读切块，保留页码与页内偏移，便于回到 bbox。"""
    chunks: list[dict] = []
    for page_no, text in enumerate(pages, start=1):
        if not text or not text.strip():
            continue
        offset = 0
        for piece in _SPLIT_RE.split(text):
            piece = piece.strip()
            if not piece:
                offset += 1
                continue
            start = text.find(piece, offset)
            if start < 0:
                start = offset
            end = start + len(piece)
            offset = end
            # 超长句再按 max_chars 硬切
            for sub_i, sub in enumerate([piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]):
                if not sub.strip():
                    continue
                chunks.append({
                    "chunk_id": f"p{page_no}_{start + sub_i * max_chars}",
                    "page": page_no,
                    "start": start + sub_i * max_chars,
                    "end": start + sub_i * max_chars + len(sub),
                    "text": sub,
                })
    return chunks


@dataclass
class DocumentIndex:
    """单份文档（code_year）的检索索引。"""

    doc_key: str
    chunks: list[dict]
    tokens: list[list[str]] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)
    avg_len: float = 1.0
    embeddings: list | None = None

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = [tokenize(c["text"]) for c in self.chunks]
        self.df = Counter()
        for toks in self.tokens:
            for t in set(toks):
                self.df[t] += 1
        self.avg_len = (sum(len(t) for t in self.tokens) / len(self.tokens)) if self.tokens else 1.0

    def search_bm25(self, query: str, *, k: int = 8) -> list[dict]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.chunks:
            return []
        n = len(self.chunks)
        k1, b = 1.5, 0.75
        scores: list[tuple[float, int]] = []
        q_counts = Counter(q_tokens)
        for i, toks in enumerate(self.tokens):
            tf = Counter(toks)
            score = 0.0
            for term, qf in q_counts.items():
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self.df.get(term, 0) + 0.5) / (self.df.get(term, 0) + 0.5))
                denom = tf[term] + k1 * (1 - b + b * len(toks) / self.avg_len)
                score += idf * (tf[term] * (k1 + 1) / denom) * qf
            if score > 0:
                scores.append((score, i))
        scores.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for score, i in scores[:k]:
            c = self.chunks[i]
            out.append({
                "score": round(score, 4),
                "page": c["page"],
                "chunk_id": c["chunk_id"],
                "snippet": _snippet(c["text"], query),
                "start": c["start"],
                "end": c["end"],
            })
        return out

    def search_embedding(self, query: str, *, k: int = 8) -> list[dict]:
        if not EMBEDDING_HOOK or not self.chunks:
            return []
        if self.embeddings is None:
            self.embeddings = EMBEDDING_HOOK([c["text"] for c in self.chunks])
        qv = EMBEDDING_HOOK([query])[0]
        scored = []
        for i, ev in enumerate(self.embeddings or []):
            score = _cosine(qv, ev)
            scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for score, i in scored[:k]:
            c = self.chunks[i]
            out.append({
                "score": round(score, 4),
                "page": c["page"],
                "chunk_id": c["chunk_id"],
                "snippet": _snippet(c["text"], query),
                "start": c["start"],
                "end": c["end"],
                "channel": "embedding",
            })
        return out

    def search(self, query: str, *, k: int = 8) -> list[dict]:
        """embedding 可用则融合；否则 BM25。命中结果仍只是检索，不是裁决。"""
        emb = self.search_embedding(query, k=k)
        if emb:
            return emb
        hits = self.search_bm25(query, k=k)
        for h in hits:
            h["channel"] = "bm25"
        return hits


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _snippet(text: str, query: str, width: int = 60) -> str:
    q = re.sub(r"\s+", "", query)
    flat = text
    compact = re.sub(r"\s+", "", text)
    pos = compact.find(q[:8]) if q else -1
    if pos < 0:
        # 取包含查询首字符的窗口
        first = q[:1] if q else ""
        pos = compact.find(first) if first else 0
        if pos < 0:
            pos = 0
    # compact 与 flat 不完全对齐，用 flat 的前段近似窗口
    return (flat[max(0, pos - 10): pos + width] or flat[:width]).strip()


# ── 进程内索引缓存（key: doc_key + 页文本指纹） ──
_INDEX_CACHE: dict[str, DocumentIndex] = {}


def build_index(doc_key: str, pages: list[str], *, cache: bool = True) -> DocumentIndex:
    fingerprint = str(len(pages)) + ":" + str(sum(len(p or "") for p in pages))
    key = f"{doc_key}#{fingerprint}"
    if cache and key in _INDEX_CACHE:
        return _INDEX_CACHE[key]
    index = DocumentIndex(doc_key=doc_key, chunks=chunk_pages(pages))
    if cache:
        _INDEX_CACHE.clear()  # 单文档会话为主，避免无界增长
        _INDEX_CACHE[key] = index
    return index


def clear_cache() -> None:
    _INDEX_CACHE.clear()
