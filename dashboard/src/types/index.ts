export interface DebugResult {
  test_name: string
  root_cause: string
  fix_suggestion: string
  fix_code?: string
  confidence?: number
}

export interface TestRun {
  id: string
  repo: string
  pr_number: number
  branch: string
  commit_sha: string
  status: 'running' | 'passed' | 'failed' | 'error'
  passed: number
  failed: number
  skipped: number
  duration_seconds: number
  created_at: string
  jira_task_id?: string | null
  error_message?: string | null
  generated_tests?: string[] | null
  debug_results?: DebugResult[] | null
  report?: string | null
}

export interface CoverageReport {
  service: string
  timestamp: string
  lines: number
  branches: number
  functions: number
  statements: number
}

export interface FlakinessScore {
  test_name: string
  file_path: string
  score: number
  failure_count: number
  run_count: number
  last_seen: string
}

export interface PullRequest {
  number: number
  title: string
  state: string
  branch: string
  base_branch: string
  commit_sha: string
  author: string
  url: string
  created_at: string
  updated_at: string
  changed_files: number
  additions: number
  deletions: number
  draft: boolean
}

export interface AgentStatus {
  name: string
  status: 'idle' | 'running' | 'error'
  last_run: string
  current_task?: string
}
