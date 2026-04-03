# Test Pyramid Strategy

## Ratios
| Layer | Target % | Framework |
|-------|----------|-----------|
| Unit | 70% | Vitest / PyTest |
| Integration | 20% | Supertest / HTTPX |
| E2E | 10% | Playwright |

## Coverage Gates
See `thresholds.json` for per-service coverage minimums.

## Flakiness Policy
- Score ≥ 70: quarantine test (skip + alert)
- Score 40–69: flag for review
- Score < 40: stable, no action
