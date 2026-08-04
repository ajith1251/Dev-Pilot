"""
Code Chunker — transforms parsed symbols and source content into semantic code chunks.

Primary strategy: split on semantic boundaries (function, class, method).
Fallback: window-based chunking for large symbols or files without parsers.
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Optional, Set

from app.core.logging import logger
from app.models.rag import (
    ChunkType,
    CodeChunk,
    CodeSymbol,
    RepositorySnapshot,
    SymbolKind,
)


# Default maximum chunk size (lines)
DEFAULT_MAX_CHUNK_LINES: int = 200

# Default minimum chunk size (lines) — avoid tiny chunks
DEFAULT_MIN_CHUNK_LINES: int = 3

# Maximum lines for a single symbol before we sub-chunk
MAX_SYMBOL_LINES_BEFORE_FALLBACK: int = 300

# Default window overlap for fallback chunking
FALLBACK_OVERLAP_LINES: int = 10

# Maximum fallback window size
FALLBACK_WINDOW_SIZE: int = 100


class CodeChunker:
    """Transforms source content and symbols into semantic code chunks.

    Chunking strategy:
    1. If symbols available: chunk at function/class/method boundaries
    2. If a symbol is too large: sub-chunk its body by inner symbols or windows
    3. If no symbols available: fallback window-based chunking
    """

    def __init__(
        self,
        max_chunk_lines: int = DEFAULT_MAX_CHUNK_LINES,
        min_chunk_lines: int = DEFAULT_MIN_CHUNK_LINES,
        max_symbol_lines: int = MAX_SYMBOL_LINES_BEFORE_FALLBACK,
        fallback_window: int = FALLBACK_WINDOW_SIZE,
        fallback_overlap: int = FALLBACK_OVERLAP_LINES,
    ) -> None:
        self.max_chunk_lines = max_chunk_lines
        self.min_chunk_lines = min_chunk_lines
        self.max_symbol_lines = max_symbol_lines
        self.fallback_window = fallback_window
        self.fallback_overlap = fallback_overlap

    def chunk_file(
        self,
        file_path: str,
        content: str,
        language: str,
        snapshot: RepositorySnapshot,
        symbols: Optional[List[CodeSymbol]] = None,
    ) -> List[CodeChunk]:
        """Create code chunks from a single file.

        Args:
            file_path: Relative path of the file.
            content: File content as string.
            language: Programming language.
            snapshot: Repository snapshot identity.
            symbols: Optional extracted symbols.

        Returns:
            List of CodeChunks.
        """
        lines = content.split("\n")
        total_lines = len(lines)
        chunks: List[CodeChunk] = []

        if not content.strip():
            return chunks

        if symbols:
            # Use semantic boundaries from symbols
            chunks = self._chunk_by_symbols(
                file_path=file_path,
                content=content,
                lines=lines,
                language=language,
                snapshot=snapshot,
                symbols=symbols,
                total_lines=total_lines,
            )
        else:
            # Fallback: window-based chunking
            chunks = self._fallback_chunk(
                file_path=file_path,
                content=content,
                lines=lines,
                language=language,
                snapshot=snapshot,
                total_lines=total_lines,
            )

        # Ensure no empty chunks and generate hashes
        valid_chunks: List[CodeChunk] = []
        for chunk in chunks:
            if chunk.end_line < chunk.start_line:
                continue
            chunk_content = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
            if not chunk_content.strip():
                continue
            chunk.content = chunk_content
            chunk.content_hash = self._hash_content(chunk_content)
            if chunk.end_line - chunk.start_line + 1 >= self.min_chunk_lines:
                valid_chunks.append(chunk)

        return valid_chunks

    def _chunk_by_symbols(
        self,
        file_path: str,
        content: str,
        lines: List[str],
        language: str,
        snapshot: RepositorySnapshot,
        symbols: List[CodeSymbol],
        total_lines: int,
    ) -> List[CodeChunk]:
        """Create chunks using symbol boundaries."""
        chunks: List[CodeChunk] = []

        # Sort symbols by start_line
        sorted_symbols = sorted(symbols, key=lambda s: s.start_line)

        # Determine module path
        module = file_path.replace("/", ".").rsplit(".", 1)[0] if "." in file_path else file_path
        module = module.replace("\\", ".")

        # Track covered line ranges to avoid overlap
        covered_lines: Set[int] = set()

        for symbol in sorted_symbols:
            if symbol.kind in {SymbolKind.IMPORT, SymbolKind.DECORATOR}:
                continue  # Don't create chunks for imports alone

            # Skip if too many lines already covered
            chunk_lines = list(range(symbol.start_line, symbol.end_line + 1))
            if all(ln in covered_lines for ln in chunk_lines):
                continue

            # Check if symbol is too large
            symbol_size = symbol.end_line - symbol.start_line + 1
            if symbol_size > self.max_symbol_lines:
                # Sub-chunk large symbol
                sub_chunks = self._sub_chunk_large_symbol(
                    file_path=file_path,
                    content=content,
                    lines=lines,
                    language=language,
                    snapshot=snapshot,
                    symbol=symbol,
                    module=module,
                )
                chunks.extend(sub_chunks)
                covered_lines.update(chunk_lines)
                continue

            # Determine chunk type
            chunk_type = self._symbol_kind_to_chunk_type(symbol.kind)

            chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, symbol.start_line, symbol.end_line)

            chunk = CodeChunk(
                chunk_id=chunk_id,
                snapshot_id=snapshot.snapshot_id,
                file_path=file_path,
                language=language,
                symbol_id=symbol.id,
                symbol_name=symbol.name,
                symbol_kind=symbol.kind,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                chunk_type=chunk_type,
                content="",  # Filled in later
                content_hash="",
                module=module,
                metadata={
                    "qualified_name": symbol.qualified_name,
                    "signature": symbol.signature or "",
                },
            )
            chunks.append(chunk)
            covered_lines.update(chunk_lines)

        # If no chunks were created from symbols, use full file as one chunk
        if not chunks and total_lines > 0:
            chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, 1, total_lines)
            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                snapshot_id=snapshot.snapshot_id,
                file_path=file_path,
                language=language,
                start_line=1,
                end_line=total_lines,
                chunk_type=ChunkType.MODULE,
                content="",
                content_hash="",
                module=module,
            ))

        return chunks

    def _sub_chunk_large_symbol(
        self,
        file_path: str,
        content: str,
        lines: List[str],
        language: str,
        snapshot: RepositorySnapshot,
        symbol: CodeSymbol,
        module: str,
    ) -> List[CodeChunk]:
        """Sub-chunk a large symbol into smaller pieces."""
        chunks: List[CodeChunk] = []

        # Find inner functions/methods if it's a class
        inner_symbols: List[CodeSymbol] = []
        # We don't have full hierarchy here, so use window-based approach

        start = symbol.start_line
        symbol_lines = symbol.end_line - symbol.start_line + 1

        if symbol_lines <= self.max_chunk_lines:
            # Just use the symbol as is
            chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, symbol.start_line, symbol.end_line)
            chunk_type = self._symbol_kind_to_chunk_type(symbol.kind)
            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                snapshot_id=snapshot.snapshot_id,
                file_path=file_path,
                language=language,
                symbol_id=symbol.id,
                symbol_name=symbol.name,
                symbol_kind=symbol.kind,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                chunk_type=chunk_type,
                content="",
                content_hash="",
                module=module,
            ))
            return chunks

        # Window-based sub-chunking
        window_size = min(self.max_chunk_lines, self.fallback_window)
        overlap = self.fallback_overlap

        pos = start
        while pos < symbol.end_line:
            end = min(pos + window_size - 1, symbol.end_line)
            chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, pos, end)
            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                snapshot_id=snapshot.snapshot_id,
                file_path=file_path,
                language=language,
                symbol_id=symbol.id,
                symbol_name=symbol.name,
                symbol_kind=symbol.kind,
                start_line=pos,
                end_line=end,
                chunk_type=ChunkType.SECTION,
                content="",
                content_hash="",
                module=module,
                metadata={"sub_chunk_of": symbol.id},
            ))
            pos = end - overlap + 1

        return chunks

    def _fallback_chunk(
        self,
        file_path: str,
        content: str,
        lines: List[str],
        language: str,
        snapshot: RepositorySnapshot,
        total_lines: int,
    ) -> List[CodeChunk]:
        """Fallback window-based chunking for files without symbols."""
        chunks: List[CodeChunk] = []

        # Try to find blank-line section boundaries
        section_boundaries = [0]  # Line indices (0-based)
        for i, line in enumerate(lines):
            if not line.strip() and i > 0:
                prev_line = lines[i - 1].strip()
                # Blank line after non-empty line indicates section boundary
                # Only if the gap is meaningful
                if prev_line and i - section_boundaries[-1] >= self.min_chunk_lines:
                    section_boundaries.append(i)
        section_boundaries.append(total_lines)

        module = file_path.replace("/", ".").rsplit(".", 1)[0] if "." in file_path else file_path
        module = module.replace("\\", ".")

        for idx in range(len(section_boundaries) - 1):
            start = section_boundaries[idx] + 1  # Convert to 1-based
            end = section_boundaries[idx + 1]
            section_lines = end - start + 1

            if section_lines > self.max_chunk_lines:
                # Further sub-divide by windows
                for sub_start in range(start, end + 1, self.max_chunk_lines):
                    sub_end = min(sub_start + self.max_chunk_lines - 1, end)
                    chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, sub_start, sub_end)
                    chunks.append(CodeChunk(
                        chunk_id=chunk_id,
                        snapshot_id=snapshot.snapshot_id,
                        file_path=file_path,
                        language=language,
                        start_line=sub_start,
                        end_line=sub_end,
                        chunk_type=ChunkType.SECTION,
                        content="",
                        content_hash="",
                        module=module,
                    ))
            else:
                chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, start, end)
                chunks.append(CodeChunk(
                    chunk_id=chunk_id,
                    snapshot_id=snapshot.snapshot_id,
                    file_path=file_path,
                    language=language,
                    start_line=start,
                    end_line=end,
                    chunk_type=ChunkType.SECTION,
                    content="",
                    content_hash="",
                    module=module,
                ))

        if not chunks:
            # Single chunk for the whole file
            chunk_id = self._make_chunk_id(snapshot.snapshot_id, file_path, 1, total_lines)
            chunks.append(CodeChunk(
                chunk_id=chunk_id,
                snapshot_id=snapshot.snapshot_id,
                file_path=file_path,
                language=language,
                start_line=1,
                end_line=total_lines,
                chunk_type=ChunkType.MODULE,
                content="",
                content_hash="",
                module=module,
            ))

        return chunks

    def _symbol_kind_to_chunk_type(self, kind: SymbolKind) -> ChunkType:
        """Map a symbol kind to the corresponding chunk type."""
        mapping = {
            SymbolKind.MODULE: ChunkType.MODULE,
            SymbolKind.CLASS: ChunkType.CLASS,
            SymbolKind.FUNCTION: ChunkType.FUNCTION,
            SymbolKind.METHOD: ChunkType.METHOD,
            SymbolKind.ASYNC_FUNCTION: ChunkType.FUNCTION,
            SymbolKind.ASYNC_METHOD: ChunkType.METHOD,
            SymbolKind.COMPONENT: ChunkType.COMPONENT,
            SymbolKind.INTERFACE: ChunkType.INTERFACE,
            SymbolKind.TYPE: ChunkType.TYPE,
        }
        return mapping.get(kind, ChunkType.SECTION)

    @staticmethod
    def _make_chunk_id(snapshot_id: str, file_path: str, start: int, end: int) -> str:
        """Create a deterministic chunk ID."""
        return f"{snapshot_id}::{file_path}::L{start}-L{end}"

    @staticmethod
    def _hash_content(content: str) -> str:
        """Create a SHA-256 hash of the content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
