"""
AST-based code analyzer using tree-sitter.
Replaces the regex-based function extraction with proper AST parsing.
Extracts: functions, classes, call graphs, imports, cyclomatic complexity.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ASTResult:
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    imports: list[str] = field(default_factory=list)
    complexity: float = 1.0  # McCabe baseline


class ASTAnalyzer:
    """
    Tree-sitter backed analyzer. Falls back to regex if tree-sitter
    grammars are not installed.
    """

    def __init__(self) -> None:
        self._py_parser = None
        self._ts_parser = None
        self._init_parsers()

    def _init_parsers(self) -> None:
        try:
            from tree_sitter import Parser
            import tree_sitter_python as tspython
            import tree_sitter_typescript as tstypescript
            from tree_sitter import Language

            self._py_parser = Parser(Language(tspython.language()))
            self._ts_parser = Parser(Language(tstypescript.language_typescript()))
            logger.info("ASTAnalyzer: tree-sitter parsers loaded")
        except Exception as exc:
            logger.warning("ASTAnalyzer: tree-sitter unavailable (%s) — using regex fallback", exc)

    # ------------------------------------------------------------------ public

    def analyze(self, source: str, language: str) -> ASTResult:
        try:
            if language == "python" and self._py_parser:
                return self._parse_python(source)
            if language in ("typescript", "javascript") and self._ts_parser:
                return self._parse_typescript(source)
        except Exception as exc:
            logger.warning("ASTAnalyzer: parse failed (%s) — using regex fallback", exc)
        return self._regex_fallback(source, language)

    # ------------------------------------------------------------------ Python

    def _parse_python(self, source: str) -> ASTResult:
        tree = self._py_parser.parse(source.encode())
        root = tree.root_node

        functions: list[str] = []
        classes: list[str] = []
        imports: list[str] = []
        call_graph: dict[str, list[str]] = {}
        complexity = 1.0

        COMPLEXITY_NODES = {
            "if_statement", "elif_clause", "for_statement", "while_statement",
            "except_clause", "conditional_expression", "boolean_operator",
        }

        def node_text(node) -> str:
            return source.encode()[node.start_byte:node.end_byte].decode(errors="replace")

        def first_child_text(node, type_: str) -> str:
            for c in node.children:
                if c.type == type_:
                    return node_text(c)
            return ""

        def extract_calls(node) -> list[str]:
            calls: list[str] = []
            for child in node.children:
                if child.type == "call":
                    fn = child.children[0] if child.children else None
                    if fn:
                        calls.append(node_text(fn).split("(")[0].strip())
                calls.extend(extract_calls(child))
            return calls

        def walk(node, current_fn: str | None = None) -> None:
            nonlocal complexity
            t = node.type
            if t in ("function_definition", "async_function_definition"):
                name = first_child_text(node, "identifier")
                if name:
                    functions.append(name)
                    calls = extract_calls(node)
                    call_graph[name] = calls
                    current_fn = name
            elif t == "class_definition":
                name = first_child_text(node, "identifier")
                if name:
                    classes.append(name)
            elif t in ("import_statement", "import_from_statement"):
                imports.append(node_text(node).replace("\n", " "))
            if t in COMPLEXITY_NODES:
                complexity += 1.0
            for child in node.children:
                walk(child, current_fn)

        walk(root)
        return ASTResult(
            functions=functions,
            classes=classes,
            call_graph=call_graph,
            imports=imports,
            complexity=complexity,
        )

    # ------------------------------------------------------------------ TypeScript

    def _parse_typescript(self, source: str) -> ASTResult:
        tree = self._ts_parser.parse(source.encode())
        root = tree.root_node

        functions: list[str] = []
        classes: list[str] = []
        imports: list[str] = []
        call_graph: dict[str, list[str]] = {}
        complexity = 1.0

        COMPLEXITY_NODES = {
            "if_statement", "for_statement", "while_statement",
            "catch_clause", "conditional_expression", "ternary_expression",
            "binary_expression",
        }

        def node_text(node) -> str:
            return source.encode()[node.start_byte:node.end_byte].decode(errors="replace")

        def extract_calls(node) -> list[str]:
            calls: list[str] = []
            for child in node.children:
                if child.type == "call_expression":
                    fn = child.children[0] if child.children else None
                    if fn:
                        raw = node_text(fn).split("(")[0].strip()
                        calls.append(raw.split(".")[-1])
                calls.extend(extract_calls(child))
            return calls

        def walk(node) -> None:
            nonlocal complexity
            t = node.type
            if t in ("function_declaration", "function_expression", "arrow_function"):
                # Try to get the name — may be from a variable declarator parent
                name = ""
                for c in node.children:
                    if c.type == "identifier":
                        name = node_text(c)
                        break
                if not name and node.parent and node.parent.type == "variable_declarator":
                    for c in node.parent.children:
                        if c.type == "identifier":
                            name = node_text(c)
                            break
                if name:
                    functions.append(name)
                    call_graph[name] = extract_calls(node)
            elif t == "method_definition":
                for c in node.children:
                    if c.type in ("property_identifier", "identifier"):
                        name = node_text(c)
                        functions.append(name)
                        call_graph[name] = extract_calls(node)
                        break
            elif t == "class_declaration":
                for c in node.children:
                    if c.type == "type_identifier":
                        classes.append(node_text(c))
                        break
            elif t == "import_statement":
                imports.append(node_text(node).replace("\n", " "))
            if t in COMPLEXITY_NODES:
                complexity += 1.0
            for child in node.children:
                walk(child)

        walk(root)
        return ASTResult(
            functions=functions,
            classes=classes,
            call_graph=call_graph,
            imports=imports,
            complexity=complexity,
        )

    # ------------------------------------------------------------------ Regex fallback

    def _regex_fallback(self, source: str, language: str) -> ASTResult:
        import re
        functions: list[str] = []
        classes: list[str] = []
        if language == "python":
            functions = re.findall(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", source, re.MULTILINE)
            classes = re.findall(r"^\s*class\s+(\w+)\s*[:(]", source, re.MULTILINE)
        else:
            functions = re.findall(
                r"(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\()",
                source,
            )
            functions = [f[0] or f[1] for f in functions if f[0] or f[1]]
            classes = re.findall(r"class\s+(\w+)", source)
        return ASTResult(functions=functions, classes=classes)
