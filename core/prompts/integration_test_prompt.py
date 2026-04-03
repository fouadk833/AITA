SYSTEM_PROMPT = """\
You are a senior backend QA engineer specializing in API integration test generation.

Rules:
- Generate ONLY valid, runnable integration test code — no explanations outside code blocks
- Tests must hit real HTTP endpoints (use Supertest for NestJS, HTTPX for FastAPI)
- Cover: success responses, validation errors (400), auth failures (401/403), not-found (404)
- Include proper setup/teardown for database state when needed
- Test request/response shapes — assert specific fields, not just status codes
- Use realistic but deterministic test data (no random values)
"""

_NESTJS_EXAMPLE = """\
describe('POST /auth/login', () => {
  it('returns 200 and JWT on valid credentials', async () => {
    const res = await request(app.getHttpServer())
      .post('/auth/login')
      .send({ email: 'test@example.com', password: 'secret' })
      .expect(200);
    expect(res.body).toHaveProperty('access_token');
  });

  it('returns 401 on wrong password', async () => {
    await request(app.getHttpServer())
      .post('/auth/login')
      .send({ email: 'test@example.com', password: 'wrong' })
      .expect(401);
  });
});
"""

_FASTAPI_EXAMPLE = """\
@pytest.mark.anyio
async def test_login_success(client: AsyncClient):
    response = await client.post('/auth/login', json={'email': 'test@example.com', 'password': 'secret'})
    assert response.status_code == 200
    assert 'access_token' in response.json()

@pytest.mark.anyio
async def test_login_wrong_password(client: AsyncClient):
    response = await client.post('/auth/login', json={'email': 'test@example.com', 'password': 'wrong'})
    assert response.status_code == 401
"""


def build_integration_test_prompt(
    code: str,
    file_path: str,
    framework: str,  # 'jest+supertest' | 'pytest+httpx'
    openapi_spec: str = "",
    jira_ticket: dict | None = None,
) -> str:
    is_nestjs = "jest" in framework or "supertest" in framework
    example = _NESTJS_EXAMPLE if is_nestjs else _FASTAPI_EXAMPLE
    lang = "typescript" if is_nestjs else "python"
    spec_section = f"\nOpenAPI spec for reference:\n```yaml\n{openapi_spec}\n```\n" if openapi_spec else ""

    jira_section = ""
    if jira_ticket:
        ac = jira_ticket.get("acceptance_criteria", "")
        jira_section = f"""\

Jira ticket: {jira_ticket['id']} — {jira_ticket['summary']}
Feature description: {jira_ticket['description'][:800]}
{"Acceptance criteria:" + chr(10) + ac if ac else ""}
"""

    return f"""\
Generate integration tests for the following {lang} controller/router code using {framework}.

File: `{file_path}`
{jira_section}{spec_section}
Code:
```{lang}
{code}
```

Example test style:
```{lang}
{example}
```

Requirements:
1. Test every route/endpoint defined in the code
2. Include both success and error cases for each endpoint
3. Assert response status codes AND response body structure
4. Include setup (beforeAll/beforeEach) to seed necessary DB data
5. Include teardown (afterAll/afterEach) to clean up
{"6. Validate that endpoints satisfy the acceptance criteria listed above" if jira_ticket else ""}

Return a single ```{lang} code block with all tests.
"""


def build_openapi_test_prompt(spec: str, framework: str) -> str:
    is_nestjs = "jest" in framework or "supertest" in framework
    lang = "typescript" if is_nestjs else "python"

    return f"""\
Given the following OpenAPI specification, generate integration tests for ALL endpoints using {framework}.

OpenAPI spec:
```yaml
{spec}
```

For each endpoint:
1. Generate at least 3 tests: success, validation error, and auth failure
2. Use realistic request bodies matching the schema
3. Assert response shapes match the spec

Return a single ```{lang} code block.
"""
