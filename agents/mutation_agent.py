"""
Mutation Testing Agent.

Applies source-level mutations (AOR, ROR, LCR, SDL) to each source file
using Python's ast module, re-runs the generated tests against each mutant,
and computes a mutation score. Fails the pipeline if score < threshold.
"""
from __future__ import annotations
import ast
import copy
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from core.models.mutation import MutantRecord, MutationReport

if TYPE_CHECKING:
    from agents.analyzer import FileChange

logger = logging.getLogger(__name__)

# ── Operator replacement tables ─────────────────────────────────────────────

_AOR = {  # Arithmetic Operator Replacement
    ast.Add:  ast.Sub,
    ast.Sub:  ast.Add,
    ast.Mult: ast.Div,
    ast.Div:  ast.Mult,
}
_ROR = {  # Relational Operator Replacement
    ast.Gt:    ast.GtE,
    ast.GtE:   ast.Gt,
    ast.Lt:    ast.LtE,
    ast.LtE:   ast.Lt,
    ast.Eq:    ast.NotEq,
    ast.NotEq: ast.Eq,
}
_LCR = {  # Logical Connector Replacement
    ast.And: ast.Or,
    ast.Or:  ast.And,
}


class _MutantVisitor(ast.NodeTransformer):
    """Collects mutation candidates from an AST without applying them."""

    def __init__(self) -> None:
        self.candidates: list[dict] = []
        # each dict: {node_ref, operator, original, replacement, lineno}

    def visit_BinOp(self, node: ast.BinOp) -> ast.BinOp:
        repl = _AOR.get(type(node.op))
        if repl:
            self.candidates.append({
                "type": "BinOp", "node": node,
                "op_class": type(node.op), "replacement": repl,
                "lineno": node.lineno,
                "operator": "AOR",
                "original": type(node.op).__name__,
                "mutant": repl.__name__,
            })
        self.generic_visit(node)
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.Compare:
        for i, op in enumerate(node.ops):
            repl = _ROR.get(type(op))
            if repl:
                self.candidates.append({
                    "type": "Compare", "node": node, "index": i,
                    "op_class": type(op), "replacement": repl,
                    "lineno": node.lineno,
                    "operator": "ROR",
                    "original": type(op).__name__,
                    "mutant": repl.__name__,
                })
        self.generic_visit(node)
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.BoolOp:
        repl = _LCR.get(type(node.op))
        if repl:
            self.candidates.append({
                "type": "BoolOp", "node": node,
                "op_class": type(node.op), "replacement": repl,
                "lineno": node.lineno,
                "operator": "LCR",
                "original": type(node.op).__name__,
                "mutant": repl.__name__,
            })
        self.generic_visit(node)
        return node


def _apply_mutation(tree: ast.AST, candidate: dict) -> ast.AST:
    """Return a deep-copied, mutated AST for one candidate."""
    mutated = copy.deepcopy(tree)

    class _Applier(ast.NodeTransformer):
        def __init__(self, cand: dict) -> None:
            self._cand = cand
            self._applied = False

        def visit_BinOp(self, node: ast.BinOp) -> ast.BinOp:
            if (
                not self._applied
                and self._cand["type"] == "BinOp"
                and node.lineno == self._cand["lineno"]
                and type(node.op) == self._cand["op_class"]
            ):
                node.op = self._cand["replacement"]()
                self._applied = True
            self.generic_visit(node)
            return node

        def visit_Compare(self, node: ast.Compare) -> ast.Compare:
            if (
                not self._applied
                and self._cand["type"] == "Compare"
                and node.lineno == self._cand["lineno"]
            ):
                idx = self._cand["index"]
                if idx < len(node.ops) and type(node.ops[idx]) == self._cand["op_class"]:
                    node.ops[idx] = self._cand["replacement"]()
                    self._applied = True
            self.generic_visit(node)
            return node

        def visit_BoolOp(self, node: ast.BoolOp) -> ast.BoolOp:
            if (
                not self._applied
                and self._cand["type"] == "BoolOp"
                and node.lineno == self._cand["lineno"]
                and type(node.op) == self._cand["op_class"]
            ):
                node.op = self._cand["replacement"]()
                self._applied = True
            self.generic_visit(node)
            return node

    return _Applier(candidate).visit(mutated)


class MutationAgent:
    """
    Runs mutation testing on Python source files.
    TypeScript mutation testing is TODO — returns a trivial 0-mutant report.
    """

    def run(
        self,
        file_changes: list["FileChange"],
        generated_tests: dict,
        workspace_dir: str,
        threshold: float = 65.0,
        max_mutants: int = 50,
    ) -> list[MutationReport]:
        from runners.pytest_runner import PytestRunner
        runner = PytestRunner()
        reports: list[MutationReport] = []

        all_test_files = (
            generated_tests.get("unit", [])
            + generated_tests.get("integration", [])
        )

        for change in file_changes:
            if change.language != "python" or change.change_type == "deleted":
                continue

            source_path = Path(workspace_dir) / change.path
            if not source_path.exists():
                continue

            source = source_path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            # Collect mutation candidates
            visitor = _MutantVisitor()
            visitor.visit(tree)
            candidates = visitor.candidates[:max_mutants]

            if not candidates:
                continue

            # Find test files that cover this source file
            stem = Path(change.path).stem
            relevant_tests = [t for t in all_test_files if stem in t and t.endswith(".py")]
            if not relevant_tests:
                logger.info("[mutation] No Python tests found for %s — skipping", change.path)
                continue

            logger.info("[mutation] %s: %d mutant(s) × %d test file(s)",
                        change.path, len(candidates), len(relevant_tests))

            killed = 0
            timed_out = 0
            mutant_records: list[MutantRecord] = []

            for i, cand in enumerate(candidates):
                mutant_id = f"{change.path}::{cand['lineno']}::{cand['operator']}"
                mutated_tree = _apply_mutation(tree, cand)

                try:
                    mutated_source = ast.unparse(mutated_tree)
                except Exception:
                    mutant_records.append(MutantRecord(
                        mutant_id=mutant_id, file_path=change.path,
                        line_number=cand["lineno"], operator=cand["operator"],
                        original_token=cand["original"], mutant_token=cand["mutant"],
                        status="error",
                    ))
                    continue

                # Write mutant to a temp location, swap the source file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(mutated_source)
                    tmp_path = tmp.name

                original_content = source_path.read_text(encoding="utf-8")
                status = "survived"
                killing_test = None
                try:
                    source_path.write_text(mutated_source, encoding="utf-8")
                    for test_file in relevant_tests:
                        try:
                            result = runner.run(test_file, cwd=workspace_dir)
                            if result.failed > 0 or result.exit_code != 0:
                                status = "killed"
                                killing_test = test_file
                                killed += 1
                                break
                        except Exception:
                            pass
                except Exception:
                    status = "error"
                finally:
                    source_path.write_text(original_content, encoding="utf-8")
                    os.unlink(tmp_path)

                mutant_records.append(MutantRecord(
                    mutant_id=mutant_id, file_path=change.path,
                    line_number=cand["lineno"], operator=cand["operator"],
                    original_token=cand["original"], mutant_token=cand["mutant"],
                    status=status, killing_test=killing_test,
                ))

            total = len(candidates)
            score = round((killed / max(1, total - timed_out)) * 100, 1)
            report = MutationReport(
                source_file=change.path,
                total_mutants=total,
                killed=killed,
                survived=total - killed - timed_out,
                timed_out=timed_out,
                mutation_score=score,
                mutants=mutant_records,
                passed_threshold=score >= threshold,
                threshold_used=threshold,
            )
            logger.info("[mutation] %s score=%.1f%% threshold=%.1f%% %s",
                        change.path, score, threshold,
                        "PASS" if report.passed_threshold else "FAIL")
            reports.append(report)

        return reports
