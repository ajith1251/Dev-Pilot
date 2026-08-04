"""
Tests for tree-sitter based parsers (Java, Go, Rust).

Verifies that each parser correctly extracts symbols and relationships
from realistic source code snippets. All parsers use static analysis
only — no code execution.
"""

from __future__ import annotations

import pytest

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphNode,
    RelationshipType,
    make_symbol_id,
)


# ═══════════════════════════════════════════════════════════════
# Java Parser Tests
# ═══════════════════════════════════════════════════════════════


class TestJavaParser:

    @pytest.fixture
    def parser(self):
        from app.code_intelligence.parsers.java_parser import JavaSymbolParser
        return JavaSymbolParser

    def test_parse_class(self, parser):
        """Parse a simple Java class with methods."""
        code = """
package com.example;

public class UserService {
    private String name;

    public String getName() {
        return this.name;
    }

    public void setName(String name) {
        this.name = name;
    }
}
"""
        p = parser("src/UserService.java", code)
        symbols, relationships, diagnostics = p.parse()

        # Should find: file + import scrap (no imports here)
        # classes: UserService, methods: getName, setName, field: name
        class_syms = [s for s in symbols if s.kind == "class"]
        method_syms = [s for s in symbols if s.kind == "method"]
        field_syms = [s for s in symbols if s.kind == "field"]

        assert len(class_syms) == 1, f"Expected 1 class, got {len(class_syms)}"
        assert class_syms[0].name == "UserService"
        assert class_syms[0].qualified_name.endswith("UserService")
        assert class_syms[0].language == "Java"

        assert len(method_syms) == 2, f"Expected 2 methods, got {len(method_syms)}"
        method_names = {m.name for m in method_syms}
        assert method_names == {"getName", "setName"}

        assert len(field_syms) >= 1
        assert field_syms[0].name == "name"

        # Verify CONTAINS relationships
        contains_rels = [r for r in relationships if r["relationship"] == RelationshipType.CONTAINS.value]
        assert len(contains_rels) >= 4  # file→class, class→getName, class→setName, class→name

    def test_parse_class_with_extends(self, parser):
        """Parse a class that extends another."""
        code = """
package com.example;

public class AdminService extends BaseService implements UserService {
    @Override
    public void doWork() {}
}
"""
        p = parser("src/AdminService.java", code)
        symbols, relationships, diagnostics = p.parse()

        class_syms = [s for s in symbols if s.kind == "class"]
        assert len(class_syms) == 1

        # Should have INHERITS and IMPLEMENTS relationships
        inherits_rels = [r for r in relationships if r["relationship"] == RelationshipType.INHERITS.value]
        assert len(inherits_rels) == 2
        targets = {r["target_id"] for r in inherits_rels}
        assert "__external__::BaseService" in targets
        assert "__external__::UserService" in targets

    def test_parse_interface(self, parser):
        """Parse an interface."""
        code = """
package com.example;

public interface Greeter {
    String greet(String name);
    void sayHello();
}
"""
        p = parser("src/Greeter.java", code)
        symbols, relationships, diagnostics = p.parse()

        iface_syms = [s for s in symbols if s.kind == "interface"]
        assert len(iface_syms) == 1
        assert iface_syms[0].name == "Greeter"

        method_syms = [s for s in symbols if s.kind in ("method", "abstract_method")]
        assert len(method_syms) >= 2

    def test_parse_imports(self, parser):
        """Parse import statements."""
        code = """
package com.example;

import java.util.List;
import java.util.ArrayList;
import java.io.*;

public class DataService {}
"""
        p = parser("src/DataService.java", code)
        symbols, relationships, diagnostics = p.parse()

        import_syms = [s for s in symbols if s.kind == "import"]
        assert len(import_syms) >= 2

        # Verify IMPORTS relationships
        imports_rels = [r for r in relationships if r["relationship"] == RelationshipType.IMPORTS.value]
        assert len(imports_rels) >= 2

    def test_parse_empty_file(self, parser):
        """Empty file should return empty results gracefully."""
        p = parser("empty.java", "")
        symbols, relationships, diagnostics = p.parse()
        assert len(symbols) == 0
        assert len(relationships) == 0

    def test_parse_malformed_code(self, parser):
        """Malformed code should not crash."""
        code = "public class Broken { missing brace"
        p = parser("Broken.java", code)
        symbols, relationships, diagnostics = p.parse()
        # Should still produce partial results or empty but not crash
        assert isinstance(symbols, list)
        assert isinstance(relationships, list)

    def test_parse_enum(self, parser):
        """Parse a Java enum."""
        code = """
package com.example;

public enum Status {
    ACTIVE,
    INACTIVE,
    PENDING
}
"""
        p = parser("Status.java", code)
        symbols, relationships, diagnostics = p.parse()

        enum_syms = [s for s in symbols if s.kind == "enum"]
        assert len(enum_syms) == 1
        assert enum_syms[0].name == "Status"


# ═══════════════════════════════════════════════════════════════
# Go Parser Tests
# ═══════════════════════════════════════════════════════════════


class TestGoParser:

    @pytest.fixture
    def parser(self):
        from app.code_intelligence.parsers.go_parser import GoSymbolParser
        return GoSymbolParser

    def test_parse_function(self, parser):
        """Parse a simple Go function."""
        code = """
package main

import "fmt"

func hello() string {
    return "Hello"
}
"""
        p = parser("main.go", code)
        symbols, relationships, diagnostics = p.parse()

        func_syms = [s for s in symbols if s.kind == "function"]
        assert len(func_syms) == 1
        assert func_syms[0].name == "hello"
        assert func_syms[0].language == "Go"

    def test_parse_struct(self, parser):
        """Parse a Go struct with fields."""
        code = """
package models

type User struct {
    Name string
    Age  int
    Email string
}
"""
        p = parser("models/user.go", code)
        symbols, relationships, diagnostics = p.parse()

        struct_syms = [s for s in symbols if s.kind == "struct"]
        assert len(struct_syms) == 1
        assert struct_syms[0].name == "User"

        field_syms = [s for s in symbols if s.kind == "field"]
        assert len(field_syms) == 3
        field_names = {f.name for f in field_syms}
        assert field_names == {"Name", "Age", "Email"}

    def test_parse_interface(self, parser):
        """Parse a Go interface."""
        code = """
package models

type Greeter interface {
    Greet() string
    Bye()
}
"""
        p = parser("models/greeter.go", code)
        symbols, relationships, diagnostics = p.parse()

        iface_syms = [s for s in symbols if s.kind == "interface"]
        assert len(iface_syms) == 1
        assert iface_syms[0].name == "Greeter"

        # Should have abstract methods for interface methods
        abs_methods = [s for s in symbols if s.kind == "abstract_method"]
        assert len(abs_methods) == 2

    def test_parse_method_receiver_pointer(self, parser):
        """Parse a Go method with pointer receiver and verify MEMBER_OF."""
        code = """
package main

type User struct {}

func (u *User) GetName() string {
    return "Alice"
}
"""
        p = parser("main.go", code)
        symbols, relationships, diagnostics = p.parse()

        method_syms = [s for s in symbols if s.kind == "method"]
        assert len(method_syms) == 1
        assert method_syms[0].name == "GetName"

        # Should have MEMBER_OF relationship to User
        member_rels = [r for r in relationships
                       if r["relationship"] == RelationshipType.MEMBER_OF.value]
        assert len(member_rels) == 1, f"Expected 1 MEMBER_OF, got {len(member_rels)}"
        assert "__external__" not in member_rels[0]["target_id"]
        assert member_rels[0]["resolution_detail"] == "method GetName belongs to User"

    def test_parse_method_receiver_value(self, parser):
        """Parse a Go method with value receiver and verify MEMBER_OF."""
        code = """
package main

type Admin struct {}

func (a Admin) Manage() string {
    return "ok"
}
"""
        p = parser("main.go", code)
        symbols, relationships, diagnostics = p.parse()

        method_syms = [s for s in symbols if s.kind == "method"]
        assert len(method_syms) == 1
        assert method_syms[0].name == "Manage"

        member_rels = [r for r in relationships
                       if r["relationship"] == RelationshipType.MEMBER_OF.value]
        assert len(member_rels) == 1, f"Expected 1 MEMBER_OF, got {len(member_rels)}"
        assert member_rels[0]["resolution_detail"] == "method Manage belongs to Admin"

    def test_parse_method_receiver_metadata(self, parser):
        """Parse Go method and check metadata contains receiver_type."""
        code = """
package main

type Service struct {}

func (s *Service) Serve() {}
"""
        p = parser("main.go", code)
        symbols, relationships, diagnostics = p.parse()

        method_syms = [s for s in symbols if s.kind == "method"]
        assert len(method_syms) == 1
        assert method_syms[0].metadata.get("receiver_type") == "Service"

    def test_parse_imports(self, parser):
        """Parse Go imports."""
        code = '''
package main

import (
    "fmt"
    "strings"
)

func main() {
    fmt.Println(strings.ToUpper("hello"))
}
'''
        p = parser("main.go", code)
        symbols, relationships, diagnostics = p.parse()

        import_syms = [s for s in symbols if s.kind == "import"]
        assert len(import_syms) >= 2

        imports_rels = [r for r in relationships if r["relationship"] == RelationshipType.IMPORTS.value]
        assert len(imports_rels) >= 2

    def test_parse_constants(self, parser):
        """Parse Go constants."""
        code = """
package config

const MaxRetries = 3
const DefaultTimeout = 30
"""
        p = parser("config.go", code)
        symbols, relationships, diagnostics = p.parse()

        const_syms = [s for s in symbols if s.kind == "constant"]
        assert len(const_syms) == 2
        const_names = {c.name for c in const_syms}
        assert const_names == {"MaxRetries", "DefaultTimeout"}


# ═══════════════════════════════════════════════════════════════
# Rust Parser Tests
# ═══════════════════════════════════════════════════════════════


class TestRustParser:

    @pytest.fixture
    def parser(self):
        from app.code_intelligence.parsers.rust_parser import RustSymbolParser
        return RustSymbolParser

    def test_parse_function(self, parser):
        """Parse a simple Rust function."""
        code = """
pub fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}
"""
        p = parser("lib.rs", code)
        symbols, relationships, diagnostics = p.parse()

        func_syms = [s for s in symbols if s.kind == "function"]
        assert len(func_syms) == 1
        assert func_syms[0].name == "greet"
        assert func_syms[0].language == "Rust"

    def test_parse_struct(self, parser):
        """Parse a Rust struct with fields."""
        code = """
pub struct User {
    pub name: String,
    pub age: u32,
    email: String,
}
"""
        p = parser("models.rs", code)
        symbols, relationships, diagnostics = p.parse()

        struct_syms = [s for s in symbols if s.kind == "struct"]
        assert len(struct_syms) == 1
        assert struct_syms[0].name == "User"

        field_syms = [s for s in symbols if s.kind == "field"]
        assert len(field_syms) == 3
        field_names = {f.name for f in field_syms}
        assert field_names == {"name", "age", "email"}

    def test_parse_enum(self, parser):
        """Parse a Rust enum with variants."""
        code = """
pub enum Status {
    Active,
    Inactive,
    Pending { reason: String },
}
"""
        p = parser("status.rs", code)
        symbols, relationships, diagnostics = p.parse()

        enum_syms = [s for s in symbols if s.kind == "enum"]
        assert len(enum_syms) == 1
        assert enum_syms[0].name == "Status"

        variants = [s for s in symbols if s.kind == "enum_variant"]
        assert len(variants) == 3
        variant_names = {v.name for v in variants}
        assert variant_names == {"Active", "Inactive", "Pending"}

    def test_parse_trait(self, parser):
        """Parse a Rust trait."""
        code = """
pub trait Greeter {
    fn greet(&self) -> String;
    fn say_bye(&self);
}
"""
        p = parser("greeter.rs", code)
        symbols, relationships, diagnostics = p.parse()

        trait_syms = [s for s in symbols if s.kind == "trait"]
        assert len(trait_syms) == 1
        assert trait_syms[0].name == "Greeter"

    def test_parse_impl(self, parser):
        """Parse a Rust impl block."""
        code = """
pub struct User {}

impl User {
    pub fn new() -> Self {
        User {}
    }

    pub fn get_name(&self) -> &str {
        "Alice"
    }
}
"""
        p = parser("user.rs", code)
        symbols, relationships, diagnostics = p.parse()

        methods = [s for s in symbols if s.kind == "method"]
        # Should have methods defined in the impl block
        assert len(methods) >= 1
        method_names = {m.name for m in methods}
        assert "new" in method_names or "get_name" in method_names

    def test_parse_use_declarations(self, parser):
        """Parse Rust use declarations."""
        code = """
use std::collections::HashMap;
use std::sync::Arc;
"""
        p = parser("lib.rs", code)
        symbols, relationships, diagnostics = p.parse()

        import_syms = [s for s in symbols if s.kind == "import"]
        assert len(import_syms) == 2

        imports_rels = [r for r in relationships if r["relationship"] == RelationshipType.IMPORTS.value]
        assert len(imports_rels) == 2

    def test_parse_const_and_static(self, parser):
        """Parse Rust const and static items."""
        code = """
pub const MAX_RETRIES: u32 = 3;
static DEFAULT_NAME: &str = "world";
"""
        p = parser("config.rs", code)
        symbols, relationships, diagnostics = p.parse()

        const_syms = [s for s in symbols if s.kind == "constant"]
        assert len(const_syms) >= 1

        static_syms = [s for s in symbols if s.kind == "static_variable"]
        assert len(static_syms) >= 1


# ═══════════════════════════════════════════════════════════════
# CodeIntelligenceService Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestParserIntegration:

    def test_service_recognizes_new_extensions(self):
        """CodeIntelligenceService should discover all 11 language extensions."""
        from app.code_intelligence.code_intelligence_service import EXT_TO_LANG

        expected = {".py": "Python", ".ts": "TypeScript", ".java": "Java",
                     ".go": "Go", ".rs": "Rust", ".c": "C", ".cpp": "C++",
                     ".cs": "C#", ".kt": "Kotlin", ".swift": "Swift",
                     ".rb": "Ruby", ".php": "PHP"}
        for ext, lang in expected.items():
            assert EXT_TO_LANG.get(ext) == lang, f"Missing {ext} -> {lang}"

    def test_service_dispatches_to_all_parsers(self):
        """_parse_file should route to the correct parser for all supported languages."""
        from app.code_intelligence.code_intelligence_service import CodeIntelligenceService

        service = CodeIntelligenceService()

        test_cases = [
            ("Test.java", "public class Test {}", "Java", "class"),
            ("main.go", "package main\nfunc hello() {}", "Go", "function"),
            ("lib.rs", "pub fn greet() {}", "Rust", "function"),
            ("test.c", "int main() { return 0; }", "C", "function"),
            ("test.cpp", "class Foo {};", "C++", "class"),
            ("test.cs", "class Foo {}", "C#", "class"),
            ("test.kt", "fun main() {}", "Kotlin", "function"),
            ("test.swift", "func hello() {}", "Swift", "function"),
            ("test.rb", "def hello; end", "Ruby", "method"),
            ("test.php", "<?php class Foo {}", "PHP", "class"),
        ]

        for fname, code, lang, expected_kind in test_cases:
            symbols, rels, diags = service._parse_file(fname, code, lang)
            kind_syms = [s for s in symbols if s.kind == expected_kind]
            assert len(kind_syms) >= 1, (
                f"{lang}: Expected at least 1 '{expected_kind}', got {len(kind_syms)}"
            )

    def test_all_11_parsers_have_supports_language(self):
        """All parsers should implement supports_language()."""
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

        for ParserCls in [CppSymbolParser, CSharpSymbolParser, GoSymbolParser,
                          JavaSymbolParser, KotlinSymbolParser, PhpSymbolParser,
                          PythonSymbolParser, RubySymbolParser, RustSymbolParser,
                          SwiftSymbolParser, TypeScriptJSParser]:
            p = ParserCls("test", "")
            assert hasattr(p, 'supports_language'), f"{ParserCls.__name__} missing supports_language"
            assert callable(p.supports_language)
