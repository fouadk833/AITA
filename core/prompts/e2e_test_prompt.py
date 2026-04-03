SYSTEM_PROMPT = """\
You are a senior frontend QA engineer specializing in Playwright E2E test generation.

Rules:
- Generate ONLY valid Playwright TypeScript test code — no explanations outside code blocks
- Use `page.getByRole`, `page.getByLabel`, `page.getByTestId` for resilient locators (not CSS selectors)
- Cover critical user flows: navigation, form submission, error states, success states
- Include `await expect(...)` assertions after every significant action
- Use `test.beforeEach` to set up auth state or navigate to the start URL
- Each test must be independent — no shared mutable state between tests
- Handle loading states with `waitForLoadState` or `waitForSelector`
"""

_EXAMPLE = """\
import { test, expect } from '@playwright/test';

test.describe('Login flow', () => {
  test('user can log in with valid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('user@example.com');
    await page.getByLabel('Password').fill('secret123');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/dashboard');
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  });

  test('shows error on wrong credentials', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('user@example.com');
    await page.getByLabel('Password').fill('wrongpassword');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByText('Invalid credentials')).toBeVisible();
  });
});
"""


def build_e2e_test_prompt(
    component_code: str,
    file_path: str,
    route: str = "",
    base_url: str = "http://localhost:3000",
) -> str:
    route_section = f"\nRoute: `{route}`\n" if route else ""

    return f"""\
Generate Playwright E2E tests for the following React component.

File: `{file_path}`
Base URL: `{base_url}`
{route_section}
Component code:
```tsx
{component_code}
```

Example test style:
```typescript
{_EXAMPLE}
```

Requirements:
1. Cover the primary user flow (happy path)
2. Cover at least one error/validation state
3. Cover navigation (page loads, redirects after action)
4. Use `page.getByRole` and `page.getByLabel` as primary locators
5. Add `await expect` assertions after each meaningful action

Return a single ```typescript code block with all tests.
"""
