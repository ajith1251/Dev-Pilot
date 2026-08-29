# Contributing to DevPilot

Thank you for your interest in contributing to DevPilot! This document provides guidelines and information for contributors.

## 🎯 How to Contribute

### Reporting Bugs

1. Check existing [issues](https://github.com/ajith1251/Dev-Pilot/issues) first
2. Open a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (Python version, OS, etc.)

### Suggesting Features

1. Open an issue with the `enhancement` label
2. Describe the feature and its use case
3. Explain how it fits into DevPilot's architecture

### Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Write or update tests
5. Ensure all tests pass
6. Commit with a clear message
7. Push to your fork
8. Open a Pull Request

## 🛠️ Development Setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (for frontend)
- PostgreSQL 18+ (optional)

### Backend Setup

```bash
# Clone and setup
git clone https://github.com/ajith1251/Dev-Pilot.git
cd Dev-Pilot/backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest -q
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

## 📝 Coding Standards

### Python (Backend)

- Follow [PEP 8](https://peps.python.org/pep-0008/)
- Use type hints for all functions
- Write docstrings for public APIs
- Keep functions focused and small
- Use meaningful variable names

```python
# Good
def calculate_test_coverage(results: List[TestResult]) -> float:
    """Calculate overall test coverage percentage."""
    pass

# Bad
def calc(r):
    pass
```

### TypeScript (Frontend)

- Use strict TypeScript
- Follow ESLint rules
- Write functional components with hooks
- Use meaningful component and variable names

```tsx
// Good
interface RunStatusProps {
  runId: string;
  status: RunStatus;
  onComplete: () => void;
}

// Bad
function Status(props) {
  return <div>{props.s}</div>;
}
```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new provider support
fix: resolve database connection issue
docs: update API documentation
test: add unit tests for planner agent
refactor: improve error handling
```

## 🧪 Testing

### Running Tests

```bash
# Backend - all tests
python -m pytest -q

# Backend - specific test file
python -m pytest tests/test_coding.py -v

# Backend - with coverage
python -m pytest --cov=app --cov-report=html

# Frontend
npm test
```

### Writing Tests

- Write tests for all new features
- Maintain or improve code coverage
- Use descriptive test names
- Follow the Arrange-Act-Assert pattern

```python
def test_planner_generates_valid_plan():
    """Test that planner generates a valid implementation plan."""
    # Arrange
    planner = PlannerAgent()
    requirements = StructuredRequirements(...)
    
    # Act
    plan = await planner.execute(requirements)
    
    # Assert
    assert plan is not None
    assert len(plan.steps) > 0
```

## 🏗️ Architecture Guidelines

### Adding a New Agent

1. Create agent in `backend/app/agents/`
2. Implement `BaseAgent[TInput, TOutput]`
3. Add tests in `backend/tests/`
4. Document in `docs/`

### Adding a New API Endpoint

1. Create route in `backend/app/api/v1/`
2. Add models in `backend/app/models/`
3. Implement service in `backend/app/services/`
4. Write tests
5. Update API documentation

### Adding a New Provider

1. Create provider in `backend/app/llm/providers/`
2. Register in `provider_registry.py`
3. Add config fields in `config.py`
4. Write tests
5. Update `docs/MULTI_PROVIDER_ROUTING.md`

## 📚 Documentation

- Update README for user-facing changes
- Update relevant docs in `docs/`
- Add inline comments for complex logic
- Include examples for new features

## 🔍 Code Review

All submissions require review before merging. We use GitHub Pull Requests for this purpose.

### Review Checklist

- [ ] Code follows project style guidelines
- [ ] Tests pass
- [ ] Documentation is updated
- [ ] No breaking changes (or clearly marked)
- [ ] Security considerations addressed

## ❓ Questions?

- Open a [Discussion](https://github.com/ajith1251/Dev-Pilot/discussions)
- Check existing documentation in `docs/`

## 📄 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to DevPilot! 🚀
