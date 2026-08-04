"""
Temporary script to verify Phase 12 code parses and imports correctly.
"""
import ast
import sys

files = [
    'app/code_intelligence/semantic_graph.py',
    'app/code_intelligence/parsers/python_parser.py',
    'app/code_intelligence/parsers/ts_parser.py',
    'app/code_intelligence/code_intelligence_service.py',
    'app/code_intelligence/impact_analyzer.py',
    'app/code_intelligence/incremental_indexer.py',
    'app/code_intelligence/graph_retriever.py',
    'app/code_intelligence/__init__.py',
    'app/code_intelligence/parsers/__init__.py',
    'app/api/v1/code_intelligence_v2.py',
    'app/cli_code_intelligence.py',
    'alembic/versions/003_add_code_intelligence.py',
]

all_ok = True
for f in files:
    try:
        with open(f, 'r') as fh:
            ast.parse(fh.read())
        print(f"OK: {f}")
    except SyntaxError as e:
        print(f"FAIL: {f}: {e}")
        all_ok = False

if all_ok:
    print("\nAll files parse OK")
else:
    print("\nSome files FAILED")
    sys.exit(1)
