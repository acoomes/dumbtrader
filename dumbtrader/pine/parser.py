"""Basic Pine Script tokenizer and parser for the supported subset.

Produces a simple AST made of plain dicts so the runtime can evaluate it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

TOKEN_PATTERNS: list[tuple[str, str]] = [
    ("COMMENT", r"//.*"),
    ("FLOAT", r"\d+\.\d+"),
    ("INT", r"\d+"),
    ("STRING", r'"[^"]*"'),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("COMMA", r","),
    ("EQ", r"=="),
    ("NEQ", r"!="),
    ("GTE", r">="),
    ("LTE", r"<="),
    ("ASSIGN", r"="),
    ("GT", r">"),
    ("LT", r"<"),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("STAR", r"\*"),
    ("SLASH", r"/"),
    ("DOT", r"\."),
    ("NEWLINE", r"\n"),
    ("WS", r"[ \t]+"),
    ("IDENT", r"[A-Za-z_]\w*"),
]

_TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_PATTERNS))


@dataclass
class Token:
    kind: str
    value: str
    line: int
    col: int = 0


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    line_start = 0
    for m in _TOKEN_RE.finditer(source):
        kind = m.lastgroup
        assert kind is not None
        value = m.group()
        col = m.start() - line_start
        if kind == "NEWLINE":
            line += 1
            line_start = m.end()
            tokens.append(Token("NEWLINE", "\n", line, 0))
            continue
        if kind in ("WS", "COMMENT"):
            continue
        tokens.append(Token(kind, value, line, col))
    return tokens


# ---------------------------------------------------------------------------
# AST node helpers
# ---------------------------------------------------------------------------

def _num(tok: Token) -> int | float:
    return float(tok.value) if tok.kind == "FLOAT" else int(tok.value)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class ParseError(Exception):
    pass


class Parser:
    """Recursive-descent parser for the Pine Script subset."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.ast: list[dict] = []

    # -- helpers -----------------------------------------------------------

    def _peek(self) -> Token | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, kind: str) -> Token:
        tok = self._peek()
        if tok is None or tok.kind != kind:
            raise ParseError(f"Expected {kind}, got {tok}")
        return self._advance()

    def _skip_newlines(self) -> None:
        while self.pos < len(self.tokens) and self.tokens[self.pos].kind == "NEWLINE":
            self.pos += 1

    def _at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    # -- top-level ---------------------------------------------------------

    def parse(self) -> list[dict]:
        while not self._at_end():
            self._skip_newlines()
            if self._at_end():
                break
            stmt = self._statement()
            if stmt is not None:
                self.ast.append(stmt)
        return self.ast

    # -- statements --------------------------------------------------------

    def _statement(self) -> dict | None:
        tok = self._peek()
        if tok is None:
            return None

        # //@version directive – skip
        if tok.kind == "SLASH":
            self._skip_to_newline()
            return None

        if tok.kind == "IDENT":
            # if / else
            if tok.value == "if":
                return self._if_statement()

            # look ahead for assignment:  ident = expr
            if self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].kind == "ASSIGN":
                return self._assignment()

            # dotted call:  strategy.entry(...)  /  ta.sma(...)
            # or plain call:  strategy(...)
            return self._expr_statement()

        self._skip_to_newline()
        return None

    def _skip_to_newline(self) -> None:
        while not self._at_end() and self.tokens[self.pos].kind != "NEWLINE":
            self.pos += 1
        if not self._at_end():
            self.pos += 1  # consume newline

    def _assignment(self) -> dict:
        name = self._advance().value  # IDENT
        self._advance()  # =
        expr = self._expression()
        self._skip_newlines()
        return {"type": "assign", "name": name, "value": expr}

    def _if_statement(self) -> dict:
        self._advance()  # consume 'if'
        cond = self._expression()
        self._skip_newlines()
        body = self._block()
        else_body: list[dict] = []
        self._skip_newlines()
        tok = self._peek()
        if tok and tok.kind == "IDENT" and tok.value == "else":
            self._advance()
            self._skip_newlines()
            else_body = self._block()
        return {"type": "if", "condition": cond, "body": body, "else": else_body}

    def _block(self) -> list[dict]:
        """Parse an indented block (lines with col > 0)."""
        stmts: list[dict] = []
        while not self._at_end():
            self._skip_newlines()
            tok = self._peek()
            if tok is None:
                break
            # Block ends when we hit a token at column 0 (top-level)
            if tok.col == 0:
                break
            stmt = self._statement()
            if stmt is not None:
                stmts.append(stmt)
        return stmts

    def _expr_statement(self) -> dict:
        expr = self._expression()
        self._skip_newlines()
        return {"type": "expr", "value": expr}

    # -- expressions -------------------------------------------------------

    def _expression(self) -> dict:
        return self._or_expr()

    def _or_expr(self) -> dict:
        left = self._and_expr()
        while self._peek() and self._peek().kind == "IDENT" and self._peek().value == "or":
            self._advance()
            right = self._and_expr()
            left = {"type": "binop", "op": "or", "left": left, "right": right}
        return left

    def _and_expr(self) -> dict:
        left = self._not_expr()
        while self._peek() and self._peek().kind == "IDENT" and self._peek().value == "and":
            self._advance()
            right = self._not_expr()
            left = {"type": "binop", "op": "and", "left": left, "right": right}
        return left

    def _not_expr(self) -> dict:
        if self._peek() and self._peek().kind == "IDENT" and self._peek().value == "not":
            self._advance()
            operand = self._not_expr()
            return {"type": "unaryop", "op": "not", "operand": operand}
        return self._comparison()

    def _comparison(self) -> dict:
        left = self._additive()
        tok = self._peek()
        if tok and tok.kind in ("GT", "LT", "GTE", "LTE", "EQ", "NEQ"):
            op = self._advance().kind.lower()
            right = self._additive()
            return {"type": "binop", "op": op, "left": left, "right": right}
        return left

    def _additive(self) -> dict:
        left = self._multiplicative()
        while self._peek() and self._peek().kind in ("PLUS", "MINUS"):
            op = self._advance().value
            right = self._multiplicative()
            left = {"type": "binop", "op": op, "left": left, "right": right}
        return left

    def _multiplicative(self) -> dict:
        left = self._unary()
        while self._peek() and self._peek().kind in ("STAR", "SLASH"):
            op = self._advance().value
            right = self._unary()
            left = {"type": "binop", "op": op, "left": left, "right": right}
        return left

    def _unary(self) -> dict:
        tok = self._peek()
        if tok and tok.kind == "MINUS":
            self._advance()
            operand = self._primary()
            return {"type": "unaryop", "op": "-", "operand": operand}
        return self._primary()

    def _primary(self) -> dict:
        tok = self._peek()
        if tok is None:
            raise ParseError("Unexpected end of input")

        # Number literals
        if tok.kind in ("INT", "FLOAT"):
            self._advance()
            return {"type": "number", "value": _num(tok)}

        # String literals
        if tok.kind == "STRING":
            self._advance()
            return {"type": "string", "value": tok.value.strip('"')}

        # Parenthesized expression
        if tok.kind == "LPAREN":
            self._advance()
            expr = self._expression()
            self._expect("RPAREN")
            return expr

        # Identifier – may be dotted name or function call
        if tok.kind == "IDENT":
            # Handle boolean literals
            if tok.value in ("true", "false"):
                self._advance()
                return {"type": "bool", "value": tok.value == "true"}

            name = self._advance().value

            # Dotted: ta.sma, strategy.entry, input.int, etc.
            while self._peek() and self._peek().kind == "DOT":
                self._advance()
                part = self._expect("IDENT").value
                name = f"{name}.{part}"

            # Function call
            if self._peek() and self._peek().kind == "LPAREN":
                self._advance()
                args = self._arg_list()
                self._expect("RPAREN")
                return {"type": "call", "name": name, "args": args}

            # Subscript: close[1]
            if self._peek() and self._peek().kind == "LBRACKET":
                self._advance()
                idx = self._expression()
                self._expect("RBRACKET")
                return {"type": "subscript", "name": name, "index": idx}

            return {"type": "ident", "name": name}

        raise ParseError(f"Unexpected token: {tok}")

    def _arg_list(self) -> list[dict]:
        args: list[dict] = []
        if self._peek() and self._peek().kind == "RPAREN":
            return args

        while True:
            # Named argument:  name=expr
            if (
                self._peek()
                and self._peek().kind == "IDENT"
                and self.pos + 1 < len(self.tokens)
                and self.tokens[self.pos + 1].kind == "ASSIGN"
            ):
                param_name = self._advance().value
                self._advance()  # =
                val = self._expression()
                args.append({"type": "kwarg", "name": param_name, "value": val})
            else:
                args.append(self._expression())

            if self._peek() and self._peek().kind == "COMMA":
                self._advance()
            else:
                break
        return args


def parse(source: str) -> list[dict]:
    """Tokenize and parse Pine Script source into an AST."""
    tokens = tokenize(source)
    return Parser(tokens).parse()
