"""Minimal, safe spreadsheet-formula evaluator for reference-workbook validation.

The healthcare benchmark workbook stores most ratios as Excel formulas. A cached
Excel result is NOT promoted on trust: the importer re-derives every formula
from the workbook's own literal inputs with this evaluator and compares the
result with the cached value. Only the small arithmetic subset the workbook
uses is supported; anything else is reported as unsupported rather than guessed.

Supported: numbers, string literals, cell references (optionally sheet- and
external-workbook-qualified, with ``$`` anchors), ``+ - * / ^``, unary minus,
comparisons, parentheses and ``IF``, ``IFERROR``, ``MAX``, ``MIN``, ``ABS``.
Excel error values propagate through arithmetic exactly as in Excel and are
caught by ``IFERROR``. Nothing here executes arbitrary code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

EXCEL_ERRORS = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A")


@dataclass(frozen=True)
class ExcelError:
    """An Excel error value (e.g. ``#NAME?``) carried through evaluation."""

    code: str

    def __str__(self) -> str:
        return self.code


class UnsupportedFormula(ValueError):
    """The formula uses syntax outside the supported subset."""


@dataclass(frozen=True)
class CellRef:
    """A parsed cell reference. ``external`` is the ``[n]`` workbook index."""

    sheet: str | None
    cell: str
    external: int | None = None

    def key(self, default_sheet: str) -> tuple[str, str]:
        return (self.sheet or default_sheet, self.cell)


_TOKEN = re.compile(
    r"""\s*(?:
        (?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
      | (?P<string>"(?:[^"]|"")*")
      | (?P<error>\#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A))
      | (?P<ref>(?:\[(?P<ext>\d+)\])?(?:(?:'(?P<qsheet>[^']+)'|(?P<sheet>[A-Za-z_][A-Za-z0-9_ .]*?))!)?
            \$?(?P<col>[A-Za-z]{1,3})\$?(?P<row>\d+)(?![A-Za-z0-9_(]))
      | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<op><=|>=|<>|[-+*/^(),<>=:])
    )""",
    re.VERBOSE,
)


def tokenize(formula: str) -> list[tuple[str, Any]]:
    text = formula[1:] if formula.startswith("=") else formula
    tokens: list[tuple[str, Any]] = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            raise UnsupportedFormula(f"Cannot tokenize formula near {text[position:position + 20]!r}.")
        position = match.end()
        if match.group("number") is not None:
            tokens.append(("number", float(match.group("number"))))
        elif match.group("string") is not None:
            tokens.append(("string", match.group("string")[1:-1].replace('""', '"')))
        elif match.group("error") is not None:
            tokens.append(("error", ExcelError(match.group("error"))))
        elif match.group("ref") is not None:
            sheet = match.group("qsheet") or match.group("sheet")
            ext = match.group("ext")
            tokens.append(
                (
                    "ref",
                    CellRef(
                        sheet=sheet,
                        cell=f"{match.group('col').upper()}{match.group('row')}",
                        external=int(ext) if ext is not None else None,
                    ),
                )
            )
        elif match.group("name") is not None:
            tokens.append(("name", match.group("name").upper()))
        else:
            tokens.append(("op", match.group("op")))
    return tokens


def references(formula: str) -> list[CellRef]:
    """Every cell reference appearing in ``formula`` (order-preserving, unique)."""
    seen: list[CellRef] = []
    for kind, value in tokenize(formula):
        if kind == "ref" and value not in seen:
            seen.append(value)
    return seen


# A resolver returns the VALUE of a referenced cell (number, string, None for a
# blank, or an ExcelError).
Resolver = Callable[[CellRef], Any]


@dataclass
class _Parser:
    tokens: list[tuple[str, Any]]
    resolve: Resolver
    position: int = 0
    trace: list[str] = field(default_factory=list)

    def peek(self) -> tuple[str, Any] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, Any]:
        token = self.peek()
        if token is None:
            raise UnsupportedFormula("Unexpected end of formula.")
        self.position += 1
        return token

    def accept(self, *ops: str) -> str | None:
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in ops:
            self.position += 1
            return token[1]
        return None

    def expect(self, op: str) -> None:
        if self.accept(op) is None:
            raise UnsupportedFormula(f"Expected {op!r}.")

    # comparison -> additive ((<|>|<=|>=|=|<>) additive)?
    def comparison(self) -> Any:
        left = self.additive()
        op = self.accept("<=", ">=", "<>", "<", ">", "=")
        if op is None:
            return left
        right = self.additive()
        for side in (left, right):
            if isinstance(side, ExcelError):
                return side
        try:
            a, b = _number(left), _number(right)
        except _ValueError:
            a, b = str(left), str(right)
        return {
            "<=": a <= b, ">=": a >= b, "<>": a != b,
            "<": a < b, ">": a > b, "=": a == b,
        }[op]

    def additive(self) -> Any:
        value = self.term()
        while (op := self.accept("+", "-")) is not None:
            value = _arith(op, value, self.term())
        return value

    def term(self) -> Any:
        value = self.power()
        while (op := self.accept("*", "/")) is not None:
            value = _arith(op, value, self.power())
        return value

    def power(self) -> Any:
        value = self.unary()
        if self.accept("^") is not None:
            value = _arith("^", value, self.power())
        return value

    def unary(self) -> Any:
        if self.accept("-") is not None:
            return _arith("-", 0.0, self.unary())
        if self.accept("+") is not None:
            return self.unary()
        return self.primary()

    def primary(self) -> Any:
        kind, value = self.take()
        if kind in {"number", "string", "error"}:
            return value
        if kind == "ref":
            token = self.peek()
            if token is not None and token == ("op", ":"):
                raise UnsupportedFormula("Cell ranges are not supported.")
            return self.resolve(value)
        if kind == "name":
            if self.accept("(") is None:
                if value in {"TRUE", "FALSE"}:
                    return value == "TRUE"
                return ExcelError("#NAME?")
            return self.call(value)
        if kind == "op" and value == "(":
            inner = self.comparison()
            self.expect(")")
            return inner
        raise UnsupportedFormula(f"Unexpected token {value!r}.")

    def arguments(self) -> list[Any]:
        """Evaluate comma-separated arguments up to the closing parenthesis."""
        args: list[Any] = []
        if self.accept(")") is not None:
            return args
        while True:
            args.append(self.comparison())
            if self.accept(")") is not None:
                return args
            self.expect(",")

    def call(self, name: str) -> Any:
        if name not in {"IF", "IFERROR", "MAX", "MIN", "ABS"}:
            # Excel evaluates an unknown function to #NAME?; arguments are
            # still consumed so parsing stays aligned.
            self.arguments()
            return ExcelError("#NAME?")
        args = self.arguments()
        if name == "IFERROR":
            if len(args) != 2:
                raise UnsupportedFormula("IFERROR takes two arguments.")
            return args[1] if isinstance(args[0], ExcelError) else args[0]
        if name == "IF":
            if len(args) not in {2, 3}:
                raise UnsupportedFormula("IF takes two or three arguments.")
            if isinstance(args[0], ExcelError):
                return args[0]
            return args[1] if _truthy(args[0]) else (args[2] if len(args) == 3 else False)
        for arg in args:
            if isinstance(arg, ExcelError):
                return arg
        try:
            numbers = [_number(a) for a in args]
        except _ValueError:
            return ExcelError("#VALUE!")
        if not numbers:
            raise UnsupportedFormula(f"{name} needs at least one argument.")
        if name == "ABS":
            return abs(numbers[0])
        return max(numbers) if name == "MAX" else min(numbers)


class _ValueError(Exception):
    pass


def _number(value: Any) -> float:
    if value is None:
        return 0.0  # Excel treats a blank cell as zero in arithmetic.
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise _ValueError


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return _number(value) != 0.0
    except _ValueError:
        return bool(value)


def _arith(op: str, left: Any, right: Any) -> Any:
    for side in (left, right):
        if isinstance(side, ExcelError):
            return side
    try:
        a, b = _number(left), _number(right)
    except _ValueError:
        return ExcelError("#VALUE!")
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return ExcelError("#DIV/0!") if b == 0 else a / b
    try:
        return a**b
    except (OverflowError, ZeroDivisionError, ValueError):
        return ExcelError("#NUM!")


def evaluate(formula: str, resolve: Resolver) -> Any:
    """Evaluate ``formula`` using ``resolve`` for cell references.

    Returns a float, bool, string or :class:`ExcelError`. Raises
    :class:`UnsupportedFormula` for syntax outside the supported subset.
    """
    parser = _Parser(tokenize(formula), resolve)
    value = parser.comparison()
    if parser.peek() is not None:
        raise UnsupportedFormula("Trailing tokens after formula.")
    return value


_OWN_COLUMN_REF = re.compile(r"(?<![A-Za-z0-9_!\]'$])\$?([A-Za-z]{1,3})(\$?\d+)(?![A-Za-z0-9_(])")


def relative_form(formula: str, own_column: str) -> str:
    """Normalize a formula so parallel columns can be compared for consistency.

    References to the formula's OWN column (unqualified, non-anchored) become
    ``{col}``; everything else is left untouched. Six industry columns built
    with the same formula therefore normalize to the same string.
    """

    def swap(match: re.Match[str]) -> str:
        if match.group(0).startswith("$"):
            return match.group(0)
        if match.group(1).upper() != own_column.upper():
            return match.group(0)
        return "{col}" + match.group(2)

    return _OWN_COLUMN_REF.sub(swap, formula)
