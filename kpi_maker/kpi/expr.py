"""Sandboxed evaluator for KPI `applies_when` expressions.

The KPI library is data, and in Mode 3 an LLM may propose entries for it. So
these expressions must never reach `eval()`. We parse with `ast` and walk an
explicit whitelist of node types — anything else raises.

Supported grammar:
    business_model.type == 'saas'
    'subscription' in business_model.revenue_model
    size.headcount_total > 50 and intent.primary_objective != 'quality'
    market.customer_count >= 100 or business_model.customer_type in ('B2B', 'B2G')
"""
from __future__ import annotations

import ast
from enum import Enum
from typing import Any

from pydantic import BaseModel

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name,
    ast.Attribute, ast.Constant, ast.Tuple, ast.List,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Load,
)


class ExpressionError(ValueError):
    pass


def _unwrap(value: Any) -> Any:
    """Enums compare as their value; models/lists resolve to comparable forms."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    return value


def _resolve(node: ast.AST, root: BaseModel) -> Any:
    """Resolve a dotted attribute chain against the profile."""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        raise ExpressionError("attribute chain must start with a name")
    parts.append(cur.id)
    parts.reverse()

    obj: Any = root
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
        if obj is None:
            return None
    return _unwrap(obj)


def _eval(node: ast.AST, root: BaseModel) -> Any:
    if not isinstance(node, _ALLOWED_NODES):
        raise ExpressionError(f"disallowed syntax: {type(node).__name__}")

    if isinstance(node, ast.Expression):
        return _eval(node.body, root)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(e, root) for e in node.elts]
    if isinstance(node, (ast.Attribute, ast.Name)):
        return _resolve(node, root)
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not):
            raise ExpressionError("only `not` is allowed as a unary operator")
        return not _eval(node.operand, root)
    if isinstance(node, ast.BoolOp):
        vals = (_eval(v, root) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, root)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, root)
            if not _compare(left, op, right):
                return False
            left = right
        return True
    raise ExpressionError(f"unhandled node: {type(node).__name__}")


def _compare(left: Any, op: ast.cmpop, right: Any) -> bool:
    # A missing field makes the expression false rather than raising: a KPI that
    # depends on data we don't have should quietly not apply.
    if left is None or right is None:
        return isinstance(op, ast.NotEq)
    try:
        if isinstance(op, ast.Eq):
            return bool(left == right)
        if isinstance(op, ast.NotEq):
            return bool(left != right)
        if isinstance(op, ast.Lt):
            return bool(left < right)
        if isinstance(op, ast.LtE):
            return bool(left <= right)
        if isinstance(op, ast.Gt):
            return bool(left > right)
        if isinstance(op, ast.GtE):
            return bool(left >= right)
        if isinstance(op, ast.In):
            return left in right
        if isinstance(op, ast.NotIn):
            return left not in right
    except TypeError:
        return False
    raise ExpressionError(f"unsupported comparison: {type(op).__name__}")


def evaluate(expression: str, profile: BaseModel) -> bool:
    """Evaluate an `applies_when` expression against a CompanyProfile."""
    if not expression or not expression.strip():
        return True
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"cannot parse {expression!r}: {exc}") from exc
    return bool(_eval(tree, profile))
