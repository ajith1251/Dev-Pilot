"""
Endpoint verification script.
Starts the FastAPI server on a random port, tests all endpoints.
Orders tests so fast endpoints are tested before long-running ones.
"""
import json
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def start_server(port):
    import uvicorn
    from app.main import app
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error')
    server = uvicorn.Server(config)
    server.run()

def test(name, url, method='GET', data=None, parser=None, timeout=5):
    global PASS, FAIL
    try:
        req = urllib.request.Request(url, method=method, data=data,
                                     headers={'Content-Type': 'application/json'} if data else {})
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read()
        d = json.loads(body)
        if parser:
            result = parser(d)
        else:
            result = 'HTTP %d' % resp.status
        print('  [%d] %s: %s' % (resp.status, name, result))
        PASS += 1
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        print('  [%d] %s: %s' % (e.code, name, body))
        FAIL += 1
        return False
    except Exception as e:
        print('  [TIMEOUT/ERR] %s: %s' % (name, e))
        FAIL += 1
        return False

def run_tests(port):
    base = 'http://127.0.0.1:%d' % port

    print()
    print('=' * 60)
    print('  DevPilot API Endpoint Verification')
    print('  Server: %s' % base)
    print('=' * 60)
    print()

    # Test quick endpoints FIRST, before any long-running ones
    print('-- Phase 1: Foundation --')
    test('GET /health', '%s/health' % base,
        parser=lambda d: 'success=%s, version=%s' % (d['success'], d['data']['version']))

    print()
    print('-- Phase 2: Capabilities (fast) --')
    test('GET /repositories/capabilities', '%s/api/v1/repositories/capabilities' % base,
        parser=lambda d: 'languages=%d' % len(d.get('languages', [])))

    print()
    print('-- Phase 4: Planning Capabilities (fast) --')
    test('GET /planning/capabilities', '%s/api/v1/planning/capabilities' % base,
        parser=lambda d: 'success=%s' % d['success'])

    # Phase 5 and 6 capabilities are instant
    print()
    print('-- Phase 5: Code Intelligence Capabilities (fast) --')
    test('GET /ci/capabilities', '%s/api/v1/code-intelligence/retrieval/capabilities' % base,
        parser=lambda d: 'success=%s' % d['success'])

    print()
    print('-- Phase 6: Coding Capabilities (fast, FIXED!) --')
    test('GET /coding/capabilities', '%s/api/v1/coding/capabilities' % base,
        parser=lambda d: 'success=%s, ops=%s' % (d['success'], d['data']['supported_operations']))

    print()
    print('-- Infrastructure --')
    test('GET /docs', '%s/docs' % base)

    # Now test the long-running endpoints
    print()
    print('-- Phase 2: Repository Analysis (may take a moment) --')
    test('POST /repositories/analyze', '%s/api/v1/repositories/analyze' % base,
        method='POST', timeout=30,
        data=json.dumps({'path': '.', 'max_depth': 3}).encode(),
        parser=lambda d: 'success=%s, name=%s, langs=%d' % (d['success'], d['data']['name'], len(d['data']['languages'])))

    print()
    print('-- Phase 5: Index Build (CPU intensive, may take time) --')
    test('POST /ci/index/build?path=.', '%s/api/v1/code-intelligence/index/build?path=.' % base,
        method='POST', timeout=60,
        parser=lambda d: 'success=%s, indexed=%s' % (d['success'], d.get('data', {}).get('files_indexed', '?')))

    # Summary
    print()
    print('=' * 60)
    print('  RESULTS: %d/%d endpoints passed' % (PASS, PASS + FAIL))
    print('=' * 60)
    print()
    return FAIL == 0

if __name__ == '__main__':
    port = find_free_port()
    print('Starting server on port %d...' % port)

    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()
    time.sleep(3)

    success = run_tests(port)
    sys.exit(0 if success else 1)
