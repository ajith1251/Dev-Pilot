"""
Create test fixture repositories for Repository Intelligence Engine tests.

Generates minimal reproducible test repos for all test scenarios.
Call from tests via create_all_fixtures().
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent


def create_all_fixtures() -> None:
    """Create all test fixture repositories."""
    create_fixture_a()
    create_fixture_b()
    create_fixture_c()
    create_fixture_d()
    create_fixture_e()
    create_fixture_f()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_fixture_a() -> None:
    """Fixture A: Next.js + TypeScript frontend project."""
    root = FIXTURES_DIR / "fixture_a_nextjs"
    if root.exists():
        shutil.rmtree(root)

    _write(root / "package.json", """{\n  "name": "my-next-app",\n  "version": "1.0.0",\n  "scripts": {\n    "dev": "next dev",\n    "build": "next build",\n    "start": "next start",\n    "lint": "next lint",\n    "typecheck": "tsc --noEmit"\n  },\n  "dependencies": {\n    "next": "^14.2.0",\n    "react": "^18.3.0",\n    "react-dom": "^18.3.0"\n  },\n  "devDependencies": {\n    "typescript": "^5.4.0",\n    "@types/react": "^18.3.0",\n    "jest": "^29.7.0",\n    "tailwindcss": "^3.4.0"\n  }\n}""")
    _write(root / "tsconfig.json", '{"compilerOptions": {"target": "es2017"}}')
    _write(root / "next.config.js", "module.exports = {}")
    _write(root / "tailwind.config.ts", "export default {}")
    _write(root / "postcss.config.js", "module.exports = {}")
    _write(root / "jest.config.js", "module.exports = {}")
    _write(root / "vitest.config.ts", "export default {}")
    _write(root / "Dockerfile", "FROM node:18\nWORKDIR /app\nCOPY . .\nRUN npm run build\nCMD npm start")
    _write(root / "README.md", "# My Next App\n\nA Next.js application.")
    _write(root / ".github/workflows/ci.yml", "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4")
    _write(root / "src/app/layout.tsx", "export default function RootLayout({children}: {children: React.ReactNode}) { return <html><body>{children}</body></html> }")
    _write(root / "src/app/page.tsx", "export default function Home() { return <h1>Hello</h1> }")
    _write(root / "src/app/globals.css", "@tailwind base;")
    _write(root / "src/components/Button.tsx", "export const Button = () => <button>Click</button>")
    _write(root / "src/components/Header.tsx", "export const Header = () => <header>Header</header>")
    _write(root / "src/__tests__/Button.test.tsx", 'import { Button } from "../components/Button";\ntest("renders", () => {});')
    _write(root / "public/logo.svg", "<svg></svg>")
    _write(root / ".env.example", "NEXT_PUBLIC_API_URL=http://localhost:3000/api")
    _write(root / ".gitignore", "node_modules\n.next\n.env\n.DS_Store")


def create_fixture_b() -> None:
    """Fixture B: FastAPI + Python backend project."""
    root = FIXTURES_DIR / "fixture_b_fastapi"
    if root.exists():
        shutil.rmtree(root)

    _write(root / "pyproject.toml", """[build-system]\nrequires = ["setuptools>=68.0"]\nbuild-backend = "setuptools.backends._legacy:_Backend"\n\n[project]\nname = "my-api"\nversion = "0.1.0"\ndependencies = ["fastapi>=0.115.0", "uvicorn>=0.30.0", "sqlalchemy>=2.0.0", "psycopg2>=2.9.0"]\n\n[tool.pytest.ini_options]\nasyncio_mode = "auto"\n""")
    _write(root / "requirements.txt", "fastapi>=0.115.0\nuvicorn>=0.30.0\nsqlalchemy>=2.0.0\npytest>=8.0.0\nhttpx>=0.27.0")
    _write(root / "README.md", "# My API\n\nA FastAPI backend application.")
    _write(root / "Dockerfile", "FROM python:3.11\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt\nCMD uvicorn app.main:app --host 0.0.0.0")
    _write(root / "docker-compose.yml", "version: '3'\nservices:\n  api:\n    build: .\n    ports:\n      - '8000:8000'\n  db:\n    image: postgres:16\n    environment:\n      POSTGRES_DB: myapi")
    _write(root / "app/__init__.py", "")
    _write(root / "app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')\ndef root(): return {'message': 'Hello'}")
    _write(root / "app/models.py", "from sqlalchemy import Column, Integer, String\nfrom sqlalchemy.orm import declarative_base\nBase = declarative_base()\nclass User(Base):\n    __tablename__ = 'users'\n    id = Column(Integer, primary_key=True)\n    name = Column(String)")
    _write(root / "app/routes/users.py", "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/users')\ndef get_users(): return []")
    _write(root / "app/services/user_service.py", "def get_user(id: int): pass")
    _write(root / "app/migrations/env.py", "# Alembic migrations")
    _write(root / "app/migrations/versions/001_init.py", "# Initial migration")
    _write(root / "tests/__init__.py", "")
    _write(root / "tests/test_api.py", "def test_health(): pass")
    _write(root / "tests/conftest.py", "import pytest\nfrom app.main import app")
    _write(root / ".env.example", "DATABASE_URL=postgresql://user:pass@localhost/mydb")
    _write(root / ".gitignore", "__pycache__\n*.pyc\n.env\nvenv\n.venv")


def create_fixture_c() -> None:
    """Fixture C: Full-stack monorepo (Next.js frontend + FastAPI backend)."""
    root = FIXTURES_DIR / "fixture_c_monorepo"
    if root.exists():
        shutil.rmtree(root)

    # Root
    _write(root / "README.md", "# Monorepo\n\nFull-stack application.")
    _write(root / "package.json", '{"name": "monorepo", "private": true, "workspaces": ["frontend", "backend"]}')

    # Frontend
    _write(root / "frontend/package.json", '{"name": "frontend", "version": "1.0.0", "scripts": {"dev": "next dev", "build": "next build", "test": "jest"}, "dependencies": {"next": "^14.2.0", "react": "^18.3.0"}, "devDependencies": {"jest": "^29.7.0", "typescript": "^5.4.0"}}')
    _write(root / "frontend/next.config.js", "module.exports = {}")
    _write(root / "frontend/tsconfig.json", "{}")
    _write(root / "frontend/src/app/page.tsx", "export default function Page() { return <h1>Frontend</h1> }")

    # Backend
    _write(root / "backend/pyproject.toml", "[project]\nname = \"backend\"\nversion = \"0.1.0\"\ndependencies = [\"fastapi>=0.115.0\", \"uvicorn>=0.30.0\", \"sqlalchemy>=2.0.0\"]")
    _write(root / "backend/requirements.txt", "fastapi>=0.115.0\npytest>=8.0.0")
    _write(root / "backend/app/main.py", "from fastapi import FastAPI\napp = FastAPI()")
    _write(root / "backend/app/models.py", "")
    _write(root / "backend/tests/test_api.py", "")
    _write(root / "backend/Dockerfile", "FROM python:3.11\nWORKDIR /app\nCOPY . .")

    # .github
    _write(root / ".github/workflows/ci.yml", "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest")


def create_fixture_d() -> None:
    """Fixture D: Unknown/minimal repository."""
    root = FIXTURES_DIR / "fixture_d_minimal"
    if root.exists():
        shutil.rmtree(root)

    _write(root / "README.md", "# Minimal Repo\n\nA minimal repository with unknown tech.")
    _write(root / "data.txt", "some data")
    _write(root / "notes.org", "* TODO Important task")
    _write(root / "script.sh", "#!/bin/bash\necho hello")


def create_fixture_e() -> None:
    """Fixture E: Malformed manifests."""
    root = FIXTURES_DIR / "fixture_e_malformed"
    if root.exists():
        shutil.rmtree(root)

    _write(root / "package.json", '{ "name": "broken", "version": "1.0.0", "dependencies": { "react": "^18.0.0" }')  # Missing closing brace
    _write(root / "pyproject.toml", "[project\nname = \"broken\"\n")  # Malformed TOML
    _write(root / "requirements.txt", "flask>=2.0.0\nnumpy\n# comment\n-non-package")  # Valid
    _write(root / "README.md", "# Broken Manifests")
    _write(root / "app.py", "print('hello')")


def create_fixture_f() -> None:
    """Fixture F: Repository with sensitive files, bins, node_modules."""
    root = FIXTURES_DIR / "fixture_f_sensitive"
    if root.exists():
        shutil.rmtree(root)

    _write(root / "README.md", "# Sensitive content test")
    _write(root / "package.json", '{"name": "sensitive-repo"}')

    # Sensitive files (names only, contents should never be exposed)
    _write(root / ".env", "OPENAI_API_KEY=sk-fake-key\nDATABASE_URL=postgres://user:pass@localhost/db")
    _write(root / "credentials.json", '{"api_key": "secret_123"}')
    _write(root / "id_rsa", "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----")

    # Ignored directories that should not be scanned
    (root / "node_modules").mkdir(parents=True)
    _write(root / "node_modules/lodash/index.js", "module.exports = {}")
    _write(root / "node_modules/react/index.js", "module.exports = {}")

    (root / ".next").mkdir()
    _write(root / ".next/build-manifest.json", "{}")

    (root / "__pycache__").mkdir()
    _write(root / "__pycache__/main.cpython-311.pyc", "")

    # Binary file (small fake)
    _write(root / "logo.png", "")  # Empty file, detected by extension

    # Valid source files
    _write(root / "src/main.py", "print('hello')")
    _write(root / "src/utils.js", "module.exports = {}")

    # .git directory (should not be scanned)
    (root / ".git/objects").mkdir(parents=True)
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")


if __name__ == "__main__":
    create_all_fixtures()
    print("All fixtures created successfully.")
