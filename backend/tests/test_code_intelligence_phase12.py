"""
Comprehensive tests for Phase 12 — Advanced Code Intelligence + Semantic Repository Graph.

Covers:
- SemanticRepositoryGraph (nodes, edges, traversal, cycles, limits)
- PythonSymbolParser (Python parsing, symbols, relationships)
- TypeScriptJSParser (TS/JS parsing, symbols, relationships)
- CodeIntelligenceService (indexing, graph access)
- ImpactAnalysisService (impact analysis, risk, tests)
- IncrementalIndexer (change detection, graph updates)
- GraphAwareRetriever (graph context, agent context)
- API endpoints
- CLI commands
- Security (no code execution, no secrets in symbols)
- Edge cases (empty files, malformed source, unsupported languages)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator, List, Tuple
from unittest.mock import AsyncMock

import pytest

from app.code_intelligence.semantic_graph import (
    ConfidenceLevel,
    GraphEdge,
    GraphNode,
    RelationshipType,
    SemanticRepositoryGraph,
    TraversalResult,
    make_symbol_id,
    normalize_qualified_name,
)
from app.code_intelligence.parsers.python_parser import PythonSymbolParser
from app.code_intelligence.parsers.ts_parser import TypeScriptJSParser
from app.code_intelligence.code_intelligence_service import CodeIntelligenceService, IndexResult
from app.code_intelligence.impact_analyzer import ImpactAnalysisService, ImpactAnalysisResult, RiskLevel
from app.code_intelligence.incremental_indexer import (
    FileChange,
    FileChangeType,
    IncrementalIndexer,
    IncrementalResult,
)
from app.code_intelligence.graph_retriever import GraphAwareRetriever, GraphRetrievalResult


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def empty_graph() -> SemanticRepositoryGraph:
    return SemanticRepositoryGraph()


@pytest.fixture
def sample_graph() -> SemanticRepositoryGraph:
    """Create a sample graph with known symbols and relationships.

    Structure:
        auth_service.py
            ├── AuthService (class)
            │   ├── login() (method)
            │   └── logout() (method)
            ├── AuthController (class)
            │   └── handle_login() (method) CALLS AuthService.login
            └── validate_token() (function)

        test_auth.py
            └── TestAuthService (class) TESTS AuthService
    """
    graph = SemanticRepositoryGraph()

    # AuthService file
    auth_file_id = make_symbol_id("auth_service.py", "auth_service.py")
    graph.add_node(GraphNode(
        id=auth_file_id, name="auth_service.py", qualified_name="auth_service.py",
        kind="file", file_path="auth_service.py", language="Python",
    ))

    auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
    graph.add_node(GraphNode(
        id=auth_svc_id, name="AuthService", qualified_name="auth_service.AuthService",
        kind="class", file_path="auth_service.py", language="Python",
        start_line=1, end_line=30,
    ))
    graph.add_edge(auth_file_id, auth_svc_id, RelationshipType.CONTAINS)

    login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
    graph.add_node(GraphNode(
        id=login_id, name="login", qualified_name="auth_service.AuthService.login",
        kind="method", file_path="auth_service.py", language="Python",
        start_line=4, end_line=8, parent_id=auth_svc_id,
        signature="def login(username: str, password: str) -> str:",
    ))
    graph.add_edge(auth_svc_id, login_id, RelationshipType.CONTAINS)

    logout_id = make_symbol_id("auth_service.py", "auth_service.AuthService.logout")
    graph.add_node(GraphNode(
        id=logout_id, name="logout", qualified_name="auth_service.AuthService.logout",
        kind="method", file_path="auth_service.py", language="Python",
        start_line=10, end_line=12, parent_id=auth_svc_id,
    ))
    graph.add_edge(auth_svc_id, logout_id, RelationshipType.CONTAINS)

    controller_id = make_symbol_id("auth_service.py", "auth_service.AuthController")
    graph.add_node(GraphNode(
        id=controller_id, name="AuthController",
        qualified_name="auth_service.AuthController",
        kind="class", file_path="auth_service.py", language="Python",
        start_line=14, end_line=22,
    ))
    graph.add_edge(auth_file_id, controller_id, RelationshipType.CONTAINS)

    handle_login_id = make_symbol_id("auth_service.py", "auth_service.AuthController.handle_login")
    graph.add_node(GraphNode(
        id=handle_login_id, name="handle_login",
        qualified_name="auth_service.AuthController.handle_login",
        kind="method", file_path="auth_service.py", language="Python",
        start_line=16, end_line=20, parent_id=controller_id,
    ))
    graph.add_edge(controller_id, handle_login_id, RelationshipType.CONTAINS)
    graph.add_edge(handle_login_id, login_id, RelationshipType.CALLS, confidence=ConfidenceLevel.HIGH)

    validate_id = make_symbol_id("auth_service.py", "auth_service.validate_token")
    graph.add_node(GraphNode(
        id=validate_id, name="validate_token",
        qualified_name="auth_service.validate_token",
        kind="function", file_path="auth_service.py", language="Python",
        start_line=24, end_line=28,
    ))
    graph.add_edge(auth_file_id, validate_id, RelationshipType.CONTAINS)

    # Test file
    test_file_id = make_symbol_id("test_auth.py", "test_auth.py")
    graph.add_node(GraphNode(
        id=test_file_id, name="test_auth.py", qualified_name="test_auth.py",
        kind="test_file", file_path="test_auth.py", language="Python",
    ))

    test_class_id = make_symbol_id("test_auth.py", "test_auth.TestAuthService")
    graph.add_node(GraphNode(
        id=test_class_id, name="TestAuthService",
        qualified_name="test_auth.TestAuthService",
        kind="test_class", file_path="test_auth.py", language="Python",
    ))
    graph.add_edge(test_file_id, test_class_id, RelationshipType.CONTAINS)
    graph.add_edge(test_class_id, auth_svc_id, RelationshipType.TESTS, confidence=ConfidenceLevel.EXACT)

    return graph


@pytest.fixture
def simple_python_source() -> str:
    return """
import os
from typing import Optional

class UserService:
    \"\"\"Service for managing users.\"\"\"

    def __init__(self, db_url: str):
        self.db_url = db_url

    def find_by_email(self, email: str) -> Optional[dict]:
        \"\"\"Find a user by email.\"\"\"
        return {"id": 1, "email": email}

    def create_user(self, name: str, email: str) -> dict:
        \"\"\"Create a new user.\"\"\"
        user = {"name": name, "email": email}
        return user


def validate_email(email: str) -> bool:
    \"\"\"Validate email format.\"\"\"
    return "@" in email
"""


@pytest.fixture
def simple_ts_source() -> str:
    return """
import { Injectable } from '@nestjs/common';
import { User } from './user.interface';

export class UserService {
    private users: User[] = [];

    async findByEmail(email: string): Promise<User | null> {
        return this.users.find(u => u.email === email) || null;
    }

    async createUser(name: string, email: string): Promise<User> {
        const user: User = { id: Date.now(), name, email };
        this.users.push(user);
        return user;
    }
}

export function validateEmail(email: string): boolean {
    return email.includes('@');
}

export interface User {
    id: number;
    name: string;
    email: string;
}
"""


@pytest.fixture
def test_repo_path() -> Generator[str, None, None]:
    """Create a temporary test repository with Python and TS files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Python module
        src_dir = Path(tmpdir) / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("")
        (src_dir / "auth.py").write_text("""
class AuthService:
    def login(self, username: str, password: str) -> str:
        return f"token_{username}"

    def logout(self, token: str) -> bool:
        return True

class AuthController:
    def __init__(self, service: AuthService):
        self.service = service

    def handle_login(self, username: str, password: str) -> dict:
        token = self.service.login(username, password)
        return {"token": token}
""")
        (src_dir / "db.py").write_text("""
from src.auth import AuthService

class Database:
    def __init__(self):
        self.connection = None

    def connect(self, url: str) -> bool:
        return True
""")

        # Test files
        tests_dir = Path(tmpdir) / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_auth.py").write_text("""
from src.auth import AuthService

class TestAuthService:
    def test_login(self):
        svc = AuthService()
        token = svc.login("user", "pass")
        assert token.startswith("token_")

    def test_logout(self):
        svc = AuthService()
        assert svc.logout("token_123")
""")

        # TypeScript file
        (src_dir / "service.ts").write_text("""
import { Database } from './db';
export class UserService {
    private db: Database;
    constructor(db: Database) {
        this.db = db;
    }
    async getUser(id: number): Promise<any> {
        return this.db.query(`SELECT * FROM users WHERE id = ${id}`);
    }
}
""")

        yield str(tmpdir)


# ═══════════════════════════════════════════════════════════════
# 1. SemanticRepositoryGraph Tests
# ═══════════════════════════════════════════════════════════════


class TestSemanticGraph:
    """Tests for the core graph abstraction."""

    def test_empty_graph(self, empty_graph):
        assert empty_graph.node_count() == 0
        assert empty_graph.edge_count() == 0

    def test_add_node(self, empty_graph):
        node = GraphNode(
            id="test::MyClass", name="MyClass", qualified_name="test.MyClass",
            kind="class", file_path="test.py", language="Python",
        )
        empty_graph.add_node(node)
        assert empty_graph.node_count() == 1
        assert empty_graph.get_node("test::MyClass") == node

    def test_add_duplicate_node(self, empty_graph):
        n1 = GraphNode(id="x::A", name="A", qualified_name="x.A", kind="class", file_path="x.py", language="Python")
        n2 = GraphNode(id="x::A", name="A", qualified_name="x.A", kind="class", file_path="x.py", language="Python", start_line=10, end_line=20)
        empty_graph.add_node(n1)
        empty_graph.add_node(n2)  # Should replace
        assert empty_graph.node_count() == 1
        assert empty_graph.get_node("x::A").end_line == 20

    def test_add_edge(self, sample_graph):
        edges = sample_graph.get_edges(
            make_symbol_id("auth_service.py", "auth_service.AuthController.handle_login"),
            make_symbol_id("auth_service.py", "auth_service.AuthService.login"),
        )
        assert len(edges) > 0
        assert edges[0].metadata.relationship == RelationshipType.CALLS
        assert edges[0].metadata.confidence == ConfidenceLevel.HIGH

    def test_add_edge_missing_source(self, empty_graph):
        empty_graph.add_node(GraphNode(id="target", name="T", qualified_name="T", kind="class", file_path="t.py", language="Python"))
        with pytest.raises(ValueError):
            empty_graph.add_edge("nonexistent", "target", RelationshipType.CALLS)

    def test_remove_node(self, sample_graph):
        node_id = make_symbol_id("auth_service.py", "auth_service.AuthService.logout")
        sample_graph.remove_node(node_id)
        assert sample_graph.get_node(node_id) is None

    def test_lookup_by_name(self, sample_graph):
        nodes = sample_graph.find_symbols_by_name("AuthService")
        assert len(nodes) == 1
        assert nodes[0].kind == "class"

    def test_lookup_by_kind(self, sample_graph):
        methods = sample_graph.symbols_by_kind("method")
        assert len(methods) >= 3  # login, logout, handle_login

    def test_symbols_in_file(self, sample_graph):
        symbols = sample_graph.symbols_in_file("auth_service.py")
        assert len(symbols) >= 5  # file + AuthService + login + logout + AuthController + handle_login + validate_token

    def test_dependencies_of(self, sample_graph):
        handle_login_id = make_symbol_id("auth_service.py", "auth_service.AuthController.handle_login")
        deps = sample_graph.dependencies_of(handle_login_id)
        assert len(deps) >= 1
        assert any(d.target_id.endswith("login") for d in deps)

    def test_dependents_of(self, sample_graph):
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        depts = sample_graph.dependents_of(login_id)
        assert len(depts) >= 1
        assert any(d.source_id.endswith("handle_login") for d in depts)

    def test_callers_of(self, sample_graph):
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        callers = sample_graph.callers_of(login_id)
        assert len(callers) >= 1

    def test_tests_for_symbol(self, sample_graph):
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        tests = sample_graph.tests_for_symbol(auth_svc_id)
        assert len(tests) >= 1

    def test_traverse_dependents(self, sample_graph):
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = sample_graph.traverse_dependents(login_id, max_depth=2, max_nodes=10)
        assert not result.truncated
        assert len(result.nodes) > 0

    def test_traverse_dependencies(self, sample_graph):
        controller_id = make_symbol_id("auth_service.py", "auth_service.AuthController")
        result = sample_graph.traverse_dependencies(controller_id, max_depth=2, max_nodes=10)
        assert len(result.nodes) > 0

    def test_traverse_neighborhood(self, sample_graph):
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        result = sample_graph.traverse_neighborhood(auth_svc_id, depth=2, max_nodes=20)
        assert len(result.nodes) > 0

    def test_traversal_limits(self, sample_graph):
        """Test that traversal respects limits."""
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        # Set very low limits
        limited_graph = SemanticRepositoryGraph(max_depth=1, max_fan_out=1, max_nodes=2)
        # Rebuild with our nodes
        for n in sample_graph.all_nodes():
            limited_graph.add_node(n)
        # Rebuild edges (simplified - just copy from sample_graph)
        for n in sample_graph.all_nodes():
            for edge in sample_graph.get_edges(n.id):
                try:
                    limited_graph.add_edge(
                        edge.source_id, edge.target_id,
                        edge.metadata.relationship, edge.metadata.confidence,
                    )
                except ValueError:
                    pass

        result = limited_graph.traverse_dependents(auth_svc_id)
        # May be truncated due to low limits
        assert result.truncated or len(result.nodes) <= 2

    def test_cycle_protection(self, empty_graph):
        """Create a cycle and ensure traversal doesn't hang."""
        a = GraphNode(id="a", name="A", qualified_name="A", kind="class", file_path="a.py", language="Python")
        b = GraphNode(id="b", name="B", qualified_name="B", kind="class", file_path="b.py", language="Python")
        empty_graph.add_node(a)
        empty_graph.add_node(b)
        empty_graph.add_edge("a", "b", RelationshipType.CALLS)
        empty_graph.add_edge("b", "a", RelationshipType.CALLS)

        result = empty_graph.traverse_dependents("a", max_depth=10, max_nodes=50)
        assert not result.truncated  # Should complete without infinite loop

    def test_serialization_roundtrip(self, sample_graph):
        data = sample_graph.to_dict()
        assert "nodes" in data
        assert "edges" in data
        assert data["stats"]["node_count"] > 0

        restored = SemanticRepositoryGraph.from_dict(data)
        assert restored.node_count() == sample_graph.node_count()
        assert restored.edge_count() == sample_graph.edge_count()

    def test_stats(self, sample_graph):
        stats = sample_graph.stats()
        assert stats["node_count"] > 0
        assert stats["edge_count"] > 0
        assert "kinds" in stats
        assert "relationships" in stats

    def test_clear(self, sample_graph):
        sample_graph.clear()
        assert sample_graph.node_count() == 0
        assert sample_graph.edge_count() == 0


# ═══════════════════════════════════════════════════════════════
# 2. PythonSymbolParser Tests
# ═══════════════════════════════════════════════════════════════


class TestPythonSymbolParser:
    """Tests for Python symbol parser."""

    def test_parse_class(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, diagnostics = parser.parse()
        assert len(diagnostics) == 0

        class_nodes = [s for s in symbols if s.kind == "class"]
        assert any(s.name == "UserService" for s in class_nodes)

    def test_parse_methods(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, _ = parser.parse()
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) >= 2
        assert any(s.name == "find_by_email" for s in methods)
        assert any(s.name == "create_user" for s in methods)

    def test_parse_functions(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, _ = parser.parse()
        funcs = [s for s in symbols if s.kind == "function"]
        assert any(s.name == "validate_email" for s in funcs)

    def test_parse_imports(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, _ = parser.parse()
        imports = [s for s in symbols if s.kind == "import"]
        assert len(imports) >= 2

    def test_relationships_contains(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, _ = parser.parse()
        contains = [r for r in relationships if r["relationship"] == "contains"]
        assert len(contains) >= 2  # class->method, module->class

    def test_empty_file(self):
        parser = PythonSymbolParser("empty.py", "")
        symbols, relationships, diagnostics = parser.parse()
        assert len(symbols) == 0
        assert len(relationships) == 0

    def test_syntax_error(self):
        parser = PythonSymbolParser("bad.py", "class Bad:\n  missing_colon\n")
        symbols, relationships, diagnostics = parser.parse()
        assert len(symbols) == 0 or len(diagnostics) <= 1  # May still extract partial symbols

    def test_class_inheritance(self):
        source = """
from typing import Protocol

class BaseService:
    pass

class UserService(BaseService):
    def get_user(self):
        pass
"""
        parser = PythonSymbolParser("services.py", source)
        symbols, relationships, _ = parser.parse()
        inherits = [r for r in relationships if r["relationship"] == "inherits"]
        assert len(inherits) >= 1

    def test_decorator_tracking(self):
        source = """
from functools import wraps

def log_call(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

class MyService:
    @property
    def name(self) -> str:
        return "MyService"

    @staticmethod
    def create() -> 'MyService':
        return MyService()
"""
        parser = PythonSymbolParser("decorators.py", source)
        symbols, relationships, _ = parser.parse()
        # Should have at least some methods extracted
        methods = [s for s in symbols if s.kind in ("method", "function")]
        assert len(methods) >= 2

    def test_qualified_names(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, _ = parser.parse()
        class_sym = next(s for s in symbols if s.name == "UserService")
        assert "user_service" in class_sym.qualified_name
        assert "UserService" in class_sym.qualified_name

    def test_docstring_extraction(self, simple_python_source):
        parser = PythonSymbolParser("user_service.py", simple_python_source)
        symbols, relationships, _ = parser.parse()
        class_sym = next(s for s in symbols if s.name == "UserService")
        assert class_sym.docstring is not None and "Service for managing users" in class_sym.docstring

    def test_constant_extraction(self):
        source = "MAX_RETRIES = 3\nDEFAULT_TIMEOUT = 30\n"
        parser = PythonSymbolParser("config.py", source)
        symbols, relationships, _ = parser.parse()
        constants = [s for s in symbols if s.kind == "constant"]
        assert len(constants) >= 1


# ═══════════════════════════════════════════════════════════════
# 3. TypeScriptJSParser Tests
# ═══════════════════════════════════════════════════════════════


class TestTypeScriptJSParser:
    """Tests for TypeScript/JavaScript symbol parser."""

    def test_parse_class(self, simple_ts_source):
        parser = TypeScriptJSParser("user.service.ts", simple_ts_source)
        symbols, relationships, diagnostics = parser.parse()
        assert len(diagnostics) == 0
        classes = [s for s in symbols if s.kind == "class"]
        assert any(s.name == "UserService" for s in classes)

    def test_parse_methods(self, simple_ts_source):
        parser = TypeScriptJSParser("user.service.ts", simple_ts_source)
        symbols, relationships, _ = parser.parse()
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) >= 2

    def test_parse_interface(self, simple_ts_source):
        parser = TypeScriptJSParser("user.service.ts", simple_ts_source)
        symbols, relationships, _ = parser.parse()
        interfaces = [s for s in symbols if s.kind == "interface"]
        assert any(s.name == "User" for s in interfaces)

    def test_parse_function(self, simple_ts_source):
        parser = TypeScriptJSParser("user.service.ts", simple_ts_source)
        symbols, relationships, _ = parser.parse()
        funcs = [s for s in symbols if s.kind == "function"]
        assert any(s.name == "validateEmail" for s in funcs)

    def test_parse_imports(self, simple_ts_source):
        parser = TypeScriptJSParser("user.service.ts", simple_ts_source)
        symbols, relationships, _ = parser.parse()
        imports = [s for s in symbols if s.kind == "import"]
        assert len(imports) >= 2

    def test_empty_file(self):
        parser = TypeScriptJSParser("empty.ts", "")
        symbols, relationships, diagnostics = parser.parse()
        assert len(symbols) == 0

    def test_javascript_file(self):
        source = """
function greet(name) {
    return `Hello, ${name}!`;
}

class Counter {
    constructor() {
        this.count = 0;
    }
    increment() {
        this.count++;
    }
}
"""
        parser = TypeScriptJSParser("counter.js", source)
        symbols, relationships, _ = parser.parse()
        classes = [s for s in symbols if s.kind == "class"]
        funcs = [s for s in symbols if s.kind == "function"]
        assert any(s.name == "Counter" for s in classes)
        assert any(s.name == "greet" for s in funcs)

    def test_jsx_component(self):
        source = """
import React from 'react';

const Header = ({ title }) => {
    return <h1>{title}</h1>;
};

export default Header;
"""
        parser = TypeScriptJSParser("Header.jsx", source)
        symbols, relationships, _ = parser.parse()
        # Should extract the arrow function as a function
        funcs = [s for s in symbols if s.kind == "function"]
        assert any("Header" in s.name for s in funcs)

    def test_tsx_component(self):
        source = """
import React from 'react';
import { User } from './types';

interface Props {
    user: User;
    onSave: () => void;
}

const UserProfile: React.FC<Props> = ({ user, onSave }) => {
    return <div>{user.name}</div>;
};

export default UserProfile;
"""
        parser = TypeScriptJSParser("UserProfile.tsx", source)
        symbols, relationships, _ = parser.parse()
        interfaces = [s for s in symbols if s.kind == "interface"]
        assert len(interfaces) >= 1

    def test_ts_enum(self):
        source = """
export enum Status {
    ACTIVE = 'active',
    INACTIVE = 'inactive',
    PENDING = 'pending'
}
"""
        parser = TypeScriptJSParser("status.ts", source)
        symbols, relationships, _ = parser.parse()
        enums = [s for s in symbols if s.kind == "enum"]
        assert len(enums) >= 1

    def test_type_alias(self):
        source = """
export type UserId = string;
export type UserData = {
    id: UserId;
    name: string;
};
"""
        parser = TypeScriptJSParser("types.ts", source)
        symbols, relationships, _ = parser.parse()
        types = [s for s in symbols if s.kind == "type"]
        assert len(types) >= 2


# ═══════════════════════════════════════════════════════════════
# 4. CodeIntelligenceService Tests
# ═══════════════════════════════════════════════════════════════


class TestCodeIntelligenceService:
    """Tests for the main code intelligence orchestrator."""

    def test_index_repository(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        result = service.index_repository(test_repo_path)
        assert result.repository_path == test_repo_path
        assert result.stats.files_parsed > 0
        assert result.stats.symbols_extracted > 0
        assert result.graph.node_count() > 0
        assert result.graph.edge_count() > 0

    def test_index_repository_stats(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        result = service.index_repository(test_repo_path)
        assert result.stats.files_scanned >= result.stats.files_parsed
        assert result.stats.duration_seconds > 0
        assert len(result.stats.languages) >= 2  # Python + TypeScript

    def test_get_graph(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        assert not service.has_graph()
        service.index_repository(test_repo_path)
        assert service.has_graph()
        graph = service.get_current_graph()
        assert graph is not None
        assert graph.node_count() > 0

    def test_find_symbol(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        service.index_repository(test_repo_path)
        node = service.find_symbol("AuthService")
        assert node is not None
        assert node.kind == "class"

    def test_symbols_in_file(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        service.index_repository(test_repo_path)
        symbols = service.symbols_in_file("src/auth.py")
        assert len(symbols) > 0

    def test_invalid_path(self):
        service = CodeIntelligenceService()
        with pytest.raises(ValueError):
            service.index_repository("/nonexistent/path")

    def test_reset(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        service.index_repository(test_repo_path)
        assert service.has_graph()
        service.reset()
        assert not service.has_graph()

    def test_index_id_generated(self, test_repo_path):
        service = CodeIntelligenceService(max_files=100)
        result = service.index_repository(test_repo_path)
        assert result.index_id is not None
        assert result.index_id.startswith("idx_")

    # ── Persist Graph Tests ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_persist_graph_calls_save_graph(self, test_repo_path):
        """Verify persist_graph() calls store.save_graph() with correct params."""
        mock_store = AsyncMock()
        mock_store.save_graph = AsyncMock(return_value={
            "index_id": "idx_test_001",
            "repository_id": "fixture_auth_app",
            "symbol_count": 10,
            "relationship_count": 5,
            "status": "active",
        })

        service = CodeIntelligenceService(max_files=100, store=mock_store)
        result = service.index_repository(test_repo_path)

        persist_result = await service.persist_graph()

        assert persist_result is not None
        assert persist_result["symbol_count"] > 0
        assert persist_result["status"] == "active"

        # Verify save_graph was called with correct arguments
        mock_store.save_graph.assert_called_once()
        call_kwargs = mock_store.save_graph.call_args[1]
        assert call_kwargs["graph"] is not None
        assert call_kwargs["index_id"] == result.index_id
        assert call_kwargs["repository_path"] == test_repo_path
        assert call_kwargs["file_count"] > 0

    @pytest.mark.asyncio
    async def test_persist_graph_no_store_returns_none(self):
        """Verify persist_graph() gracefully returns None with no store."""
        service = CodeIntelligenceService(max_files=100)
        result = await service.persist_graph()
        assert result is None

    @pytest.mark.asyncio
    async def test_persist_graph_no_graph_returns_none(self):
        """Verify persist_graph() gracefully returns None with no graph."""
        mock_store = AsyncMock()
        service = CodeIntelligenceService(max_files=100, store=mock_store)
        # Don't index anything
        result = await service.persist_graph()
        assert result is None
        mock_store.save_graph.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_graph_store_error_returns_none(self, test_repo_path):
        """Verify persist_graph() gracefully handles store errors."""
        mock_store = AsyncMock()
        mock_store.save_graph = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        service = CodeIntelligenceService(max_files=100, store=mock_store)
        service.index_repository(test_repo_path)

        result = await service.persist_graph()
        assert result is None  # Graceful degradation
        mock_store.save_graph.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 5. ImpactAnalysisService Tests
# ═══════════════════════════════════════════════════════════════


class TestImpactAnalysisService:
    """Tests for impact analysis."""

    def test_analyze_no_graph(self):
        service = ImpactAnalysisService(graph=None, max_depth=2, max_nodes=50)
        result = service.analyze(["symbol_1"])
        assert result.warning is not None

    def test_impact_analysis(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=2, max_nodes=50)
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = service.analyze([login_id])
        assert len(result.direct_impact) >= 1
        assert len(result.root_symbols) >= 1

    def test_direct_impact(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=2, max_nodes=50)
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = service.analyze([login_id])
        direct_names = [i.node.name for i in result.direct_impact]
        # handle_login directly calls login
        assert any("handle_login" in n for n in direct_names)

    def test_related_tests(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=3, max_nodes=50)
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        result = service.analyze([auth_svc_id])
        test_names = [t.name for t in result.related_tests]
        assert any("TestAuthService" in n for n in test_names)

    def test_risk_assessment(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=2, max_nodes=50)
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = service.analyze([login_id])
        total_risk = sum(result.risk_summary.values())
        assert total_risk >= len(result.direct_impact) + len(result.indirect_impact)

    def test_impact_files(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=3, max_nodes=50)
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        result = service.analyze([auth_svc_id])
        assert len(result.affected_files) >= 2  # auth_service.py + test_auth.py

    def test_analyze_files(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=2, max_nodes=50)
        result = service.analyze_files(["auth_service.py"])
        assert len(result.affected_files) >= 1

    def test_summary(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=2, max_nodes=50)
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = service.analyze([login_id])
        summary = service.summarize(result)
        assert "Impact Analysis" in summary

    def test_multiple_symbols_impact(self, sample_graph):
        service = ImpactAnalysisService(graph=sample_graph, max_depth=2, max_nodes=50)
        ids = [
            make_symbol_id("auth_service.py", "auth_service.AuthService.login"),
            make_symbol_id("auth_service.py", "auth_service.AuthService.logout"),
        ]
        result = service.analyze(ids)
        assert len(result.root_symbols) == 2


# ═══════════════════════════════════════════════════════════════
# 6. IncrementalIndexer Tests
# ═══════════════════════════════════════════════════════════════


class TestIncrementalIndexer:
    """Tests for incremental indexing."""

    def test_detect_added_file(self):
        indexer = IncrementalIndexer()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file
            Path(tmpdir, "test.py").write_text("x = 1")
            changes = indexer.detect_changes(tmpdir)
            added = [c for c in changes if c.change_type == FileChangeType.ADDED]
            assert len(added) >= 1

    def test_detect_modified_file(self):
        indexer = IncrementalIndexer()
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("x = 1")
            # Index first time
            changes = indexer.detect_changes(tmpdir)
            for c in changes:
                indexer.set_file_hash(c.file_path, c.new_hash or "")

            # Modify file
            Path(tmpdir, "test.py").write_text("x = 2")
            changes2 = indexer.detect_changes(tmpdir)
            modified = [c for c in changes2 if c.change_type == FileChangeType.MODIFIED]
            assert len(modified) >= 1

    def test_detect_deleted_file(self):
        indexer = IncrementalIndexer()
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("x = 1")
            # Record hash
            changes = indexer.detect_changes(tmpdir)
            for c in changes:
                indexer.set_file_hash(c.file_path, c.new_hash or "")

            # Delete file
            os.remove(Path(tmpdir, "test.py"))
            changes2 = indexer.detect_changes(tmpdir)
            deleted = [c for c in changes2 if c.change_type == FileChangeType.DELETED]
            assert len(deleted) >= 1

    def test_detect_unchanged_file(self):
        indexer = IncrementalIndexer()
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("x = 1")
            changes = indexer.detect_changes(tmpdir)
            for c in changes:
                indexer.set_file_hash(c.file_path, c.new_hash or "")

            # No modification
            changes2 = indexer.detect_changes(tmpdir)
            unchanged = [c for c in changes2 if c.change_type == FileChangeType.UNCHANGED]
            assert len(unchanged) >= 1

    def test_update_graph_add(self):
        indexer = IncrementalIndexer()
        graph = SemanticRepositoryGraph()
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("class A: pass\n")
            changes = indexer.detect_changes(tmpdir)
            result = indexer.update_graph(graph, tmpdir, changes)
            assert result.indexed > 0 or result.updated > 0

    def test_update_graph_delete(self):
        indexer = IncrementalIndexer()
        graph = SemanticRepositoryGraph()
        # Pre-populate graph
        graph.add_node(GraphNode(
            id="test.py::test.A",
            name="A", qualified_name="test.A", kind="class",
            file_path="test.py", language="Python",
        ))
        indexer.set_file_hash("test.py", "oldhash")

        with tempfile.TemporaryDirectory() as tmpdir:
            # File was deleted
            changes = [FileChange("test.py", FileChangeType.DELETED, old_hash="oldhash")]
            result = indexer.update_graph(graph, tmpdir, changes)
            assert graph.get_node("test.py::test.A") is None

    def test_parse_file_python(self):
        node_id = make_symbol_id("app.py", "app")
        symbols, relationships, diagnostics = IncrementalIndexer._parse_file("app.py", "class App: pass\n", "Python")
        assert len(symbols) >= 1


# ═══════════════════════════════════════════════════════════════
# 7. GraphAwareRetriever Tests
# ═══════════════════════════════════════════════════════════════


class TestGraphAwareRetriever:
    """Tests for graph-aware retrieval."""

    def test_retrieve_by_symbol(self, sample_graph):
        retriever = GraphAwareRetriever(graph=sample_graph)
        login_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = retriever.retrieve_for_symbols([login_id])
        assert len(result.direct_matches) >= 1
        assert result.direct_matches[0].node.name == "login"

    def test_retrieve_graph_expansion(self, sample_graph):
        retriever = GraphAwareRetriever(graph=sample_graph)
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        result = retriever.retrieve_for_symbols([auth_svc_id])
        # Should find related symbols (methods, test, etc.)
        assert len(result.graph_context) >= 1

    def test_retrieve_for_file(self, sample_graph):
        retriever = GraphAwareRetriever(graph=sample_graph)
        result = retriever.retrieve_for_file("auth_service.py")
        assert len(result.direct_matches) >= 1

    def test_agent_context(self, sample_graph):
        retriever = GraphAwareRetriever(graph=sample_graph)
        context = retriever.get_agent_context(["AuthService"])
        assert "Graph Context" in context
        assert "AuthService" in context

    def test_no_graph(self):
        retriever = GraphAwareRetriever(graph=None)
        result = retriever.retrieve_for_symbols(["test"])
        assert len(result.warnings) > 0

    def test_truncation(self, sample_graph):
        retriever = GraphAwareRetriever(graph=sample_graph)
        # Request very small expansion
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService.login")
        result = retriever.retrieve_for_symbols(
            [auth_svc_id],
            expand_depth=0,  # No expansion
        )
        # Should have direct matches but limited graph context
        assert len(result.direct_matches) >= 1

    def test_score_ordering(self, sample_graph):
        retriever = GraphAwareRetriever(graph=sample_graph)
        auth_svc_id = make_symbol_id("auth_service.py", "auth_service.AuthService")
        result = retriever.retrieve_for_symbols([auth_svc_id], max_expanded=10)
        if len(result.graph_context) >= 2:
            # Higher priority items should score higher
            scores = [i.relevance_score for i in result.graph_context]
            assert scores == sorted(scores, reverse=True)


# ═══════════════════════════════════════════════════════════════
# 8. Edge Case Tests
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_source_file(self):
        parser = PythonSymbolParser("empty.py", "")
        symbols, rels, diags = parser.parse()
        assert len(symbols) == 0

    def test_empty_ts_file(self):
        parser = TypeScriptJSParser("empty.ts", "")
        symbols, rels, diags = parser.parse()
        assert len(symbols) == 0

    def test_minified_file_resilience(self):
        """Minified files should not crash the parser."""
        minified = "function a(b){return b+1}function c(d){return d-1}"
        parser = TypeScriptJSParser("bundle.js", minified)
        symbols, rels, diags = parser.parse()
        # Should extract some symbols without crashing
        assert len(diags) == 0 or len(symbols) > 0

    def test_very_large_line(self):
        source = "x = " + "a" * 10000 + "\n"
        parser = PythonSymbolParser("large.py", source)
        symbols, rels, diags = parser.parse()
        # Should not crash

    def test_malformed_python(self):
        source = "class Incomplete:\n  def method(\n"
        parser = PythonSymbolParser("bad.py", source)
        symbols, rels, diags = parser.parse()
        # May produce partial results or errors, but shouldn't crash

    def test_unsupported_language(self):
        symbols, relationships, diagnostics = IncrementalIndexer._parse_file("file.rs", "fn main() {}", "Rust")
        assert len(diagnostics) >= 1

    def test_no_code_execution(self):
        """Parser must not execute repository code."""
        source = """
import subprocess
import os

result = subprocess.check_output(['rm', '-rf', '/'])
os.system('format c:')

class Safe:
    pass
"""
        parser = PythonSymbolParser("dangerous.py", source)
        symbols, rels, diags = parser.parse()
        # Should parse without executing anything
        assert any(s.name == "Safe" for s in symbols)

    def test_no_secrets_in_symbols(self):
        source = """
SECRET_KEY = "super_secret_12345"
API_TOKEN = "tok_abc123"
class Config:
    PASSWORD = "hunter2"
"""
        parser = PythonSymbolParser("secrets.py", source)
        symbols, rels, diags = parser.parse()
        # Symbol metadata should not contain actual secrets
        for sym in symbols:
            meta_str = str(sym.metadata).lower()
            assert "super_secret_12345" not in meta_str
            assert "hunter2" not in meta_str

    def test_special_characters_in_source(self):
        source = "class ÜberService:\n    pass\n"
        parser = PythonSymbolParser("unicode.py", source)
        symbols, rels, diags = parser.parse()
        # Should handle unicode
        class_names = [s.name for s in symbols if s.kind == "class"]
        assert any("Service" in n for n in class_names) or len(diags) <= 1

    def test_binary_file_handling(self):
        """Binary files should be skipped during indexing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            binary_path = Path(tmpdir) / "image.png"
            binary_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            # This shouldn't crash CodeIntelligenceService
            from app.code_intelligence.code_intelligence_service import CodeIntelligenceService
            # Binary files are skipped during _discover_files
            files = CodeIntelligenceService(max_files=100)._discover_files(tmpdir)
            # .png files should be skipped (not in EXT_TO_LANG)
            assert not any("image.png" in f[0] for f in files)

    def test_concurrent_graph_access(self, sample_graph):
        """Graph should handle concurrent reads."""
        import threading
        results = []

        def read_graph():
            results.append(sample_graph.node_count())
            results.append(sample_graph.edge_count())

        threads = [threading.Thread(target=read_graph) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10  # 5 threads × 2 results each
        assert all(r > 0 for r in results)


# ═══════════════════════════════════════════════════════════════
# 9. API Tests
# ═══════════════════════════════════════════════════════════════


class TestCodeIntelligenceV2API:
    """Tests for Phase 12 API endpoints."""

    def test_capabilities_endpoint_exists(self):
        """Test that the capabilities endpoint can be constructed."""
        from app.api.v1.code_intelligence_v2 import router
        routes = [r.path for r in router.routes]
        assert "/api/v1/code-intelligence-v2/capabilities" in routes

    def test_endpoints_exist(self):
        """Verify expected API endpoints are registered."""
        from app.api.v1.code_intelligence_v2 import router
        routes = [r.path for r in router.routes]
        expected = [
            "/api/v1/code-intelligence-v2/index",
            "/api/v1/code-intelligence-v2/status",
            "/api/v1/code-intelligence-v2/symbols",
            "/api/v1/code-intelligence-v2/symbol/{symbol_id}",
            "/api/v1/code-intelligence-v2/impact",
            "/api/v1/code-intelligence-v2/retrieve",
            "/api/v1/code-intelligence-v2/capabilities",
            "/api/v1/code-intelligence-v2/index/reset",
        ]
        for path in expected:
            assert path in routes, f"Missing endpoint: {path}"


# ═══════════════════════════════════════════════════════════════
# 10. Model Tests
# ═══════════════════════════════════════════════════════════════


class TestGraphModels:
    """Tests for graph data models."""

    def test_graph_node_creation(self):
        node = GraphNode(
            id="test::A", name="A", qualified_name="test.A",
            kind="class", file_path="test.py", language="Python",
            start_line=1, end_line=10,
            signature="class A:", docstring="Test class",
            metadata={"key": "value"},
        )
        assert node.id == "test::A"
        assert node.name == "A"
        assert node.kind == "class"
        assert node.metadata["key"] == "value"

    def test_graph_edge_creation(self):
        meta = GraphEdge(
            source_id="a", target_id="b",
            metadata=type('EdgeMeta', (), {
                'relationship': RelationshipType.CALLS,
                'confidence': ConfidenceLevel.EXACT,
                'source_lines': [1],
                'resolution_detail': "test",
                'weight': 1.0,
                'metadata': {},
            })(),
        )
        assert meta.source_id == "a"
        assert meta.target_id == "b"

    def test_edge_validation(self):
        with pytest.raises(ValueError):
            GraphEdge(
                source_id="", target_id="b",
                metadata=type('EdgeMeta', (), {
                    'relationship': RelationshipType.CALLS,
                    'confidence': ConfidenceLevel.EXACT,
                    'source_lines': None,
                    'resolution_detail': None,
                    'weight': 1.0,
                    'metadata': {},
                })(),
            )

    def test_make_symbol_id(self):
        sid = make_symbol_id("auth.py", "auth.AuthService.login")
        assert sid == "auth.py::auth.AuthService.login"

    def test_normalize_qualified_name(self):
        qn = normalize_qualified_name("services/auth.py", "AuthService")
        assert "services" in qn
        assert "AuthService" in qn

    def test_traversal_result(self):
        result = TraversalResult(truncated=True)
        assert result.truncated

    def test_relationship_type_values(self):
        assert RelationshipType.CALLS.value == "calls"
        assert RelationshipType.INHERITS.value == "inherits"
        assert RelationshipType.TESTS.value == "tests"
        assert RelationshipType.CONTAINS.value == "contains"

    def test_confidence_level_values(self):
        assert ConfidenceLevel.EXACT.value == "exact"
        assert ConfidenceLevel.HIGH.value == "high"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.UNRESOLVED.value == "unresolved"

    def test_risk_level_values(self):
        assert RiskLevel.CRITICAL.value == "critical"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.LOW.value == "low"
