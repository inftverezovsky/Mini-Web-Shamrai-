# Telegram proxy failover — TDD evidence

## Source and journey

No external plan file was used. The incident-derived journey was: as the Shamrai operator, I want Telegram API calls to switch to another configured proxy when the active proxy is unreachable, so that bot polling and notifications recover without exposing credentials or duplicating ambiguous sends.

## RED evidence

- `python -m unittest tests.test_telegram_proxy_failover -v`
- Initial result: 8 errors because the proxy-pool setting, validation, selector, and failover behavior did not exist.
- Review regression result: 2 expected failures proved that legacy `HTTPS_PROXY` was not rejected and direct safe connection failures were not retried.

## GREEN evidence

- `.venv/Scripts/python.exe -m unittest tests.test_telegram_proxy_failover tests.test_telegram_delivery_guards tests.test_delivery_outbox -q`
- Result: 45 tests passed.
- `.venv/Scripts/python.exe -m pytest -q`
- Result: 449 tests passed.
- `.venv/Scripts/python.exe -m compileall -q src alembic`
- Result: passed.
- `.venv/Scripts/python.exe -c "import src.main; print('backend_import_ok')"`
- Result: `backend_import_ok`.

## Test specification

| Guarantee | Test type | Result |
|---|---|---|
| Proxy configuration is a strict JSON array and HTTP transport needs explicit opt-in | Unit | PASS |
| Credentialed proxy configuration is redacted from settings repr/JSON | Security unit | PASS |
| Legacy process-wide `HTTPS_PROXY` is rejected | Security unit | PASS |
| Connection-refused and proxy-auth failures switch to the next proxy | Unit | PASS |
| A successful proxy becomes the preferred proxy for later calls | Unit | PASS |
| Ambiguous timeouts and Telegram 429 responses are not resent | Safety unit | PASS |
| Direct mode retains retries for safe pre-connect failures | Regression unit | PASS |
| Multipart delivery follows the same safe failover rules | Unit | PASS |
| Existing Telegram guards and outbox behavior remain compatible | Integration | PASS |

## Coverage and known gaps

The workspace virtual environment does not include `coverage` or `pytest-cov`, so a numeric coverage report was not available. The focused suite exercises configuration, JSON and multipart transports, safe/unsafe failure classification, stickiness, direct fallback, redaction, health guards, and outbox dispatch. Runtime proxy reachability and Bot API probes are verified separately during deployment.
