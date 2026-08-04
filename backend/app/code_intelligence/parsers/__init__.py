"""
Language parsers for Phase 12 code intelligence.

Each parser extracts symbols and relationships from source code
using static analysis only — never executing repository code.

Supported languages (11 total):
- Python (stdlib AST)
- TypeScript / JavaScript (regex + brace-matching)
- Java, Go, Rust (tree-sitter)
- C/C++, C#, Kotlin, Swift, Ruby, PHP (tree-sitter)
"""

from app.code_intelligence.parsers.c_cpp_parser import CppSymbolParser
from app.code_intelligence.parsers.csharp_parser import CSharpSymbolParser
from app.code_intelligence.parsers.go_parser import GoSymbolParser
from app.code_intelligence.parsers.java_parser import JavaSymbolParser
from app.code_intelligence.parsers.kotlin_parser import KotlinSymbolParser
from app.code_intelligence.parsers.php_parser import PhpSymbolParser
from app.code_intelligence.parsers.python_parser import PythonSymbolParser
from app.code_intelligence.parsers.ruby_parser import RubySymbolParser
from app.code_intelligence.parsers.rust_parser import RustSymbolParser
from app.code_intelligence.parsers.swift_parser import SwiftSymbolParser
from app.code_intelligence.parsers.ts_parser import TypeScriptJSParser

__all__ = [
    "CppSymbolParser",
    "CSharpSymbolParser",
    "GoSymbolParser",
    "JavaSymbolParser",
    "KotlinSymbolParser",
    "PhpSymbolParser",
    "PythonSymbolParser",
    "RubySymbolParser",
    "RustSymbolParser",
    "SwiftSymbolParser",
    "TypeScriptJSParser",
]
