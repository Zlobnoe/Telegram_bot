"""Calculator skill — safely evaluates math expressions."""
from __future__ import annotations

import ast
import math
import operator
import re

# safe operators
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

SAFE_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "pi": math.pi,
    "e": math.e,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value}")
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op = OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op = OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(operand)
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_FUNCS:
            func = SAFE_FUNCS[node.func.id]
            if callable(func):
                args = [_eval_node(a) for a in node.args]
                return func(*args)
        raise ValueError(f"Unsupported function: {ast.dump(node.func)}")
    elif isinstance(node, ast.Name):
        if node.id in SAFE_FUNCS:
            val = SAFE_FUNCS[node.id]
            if not callable(val):
                return val
        raise ValueError(f"Unknown name: {node.id}")
    elif isinstance(node, ast.Expression):
        return _eval_node(node.body)
    raise ValueError(f"Unsupported node: {type(node).__name__}")


def safe_eval(expr: str) -> float:
    """Safely evaluate a mathematical expression."""
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree)


def execute(query: str, **kwargs) -> str:
    """Extract math expression from query and evaluate it."""
    # try to extract expression: remove common words
    expr = query.strip()
    # remove command prefix
    for prefix in ("/calc ", "посчитай ", "вычисли ", "calculate ", "compute "):
        if expr.lower().startswith(prefix):
            expr = expr[len(prefix):]
            break

    # replace common symbols
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    # replace percentage pattern: "15% of 250" → "0.15 * 250"
    pct = re.match(r"([\d.]+)\s*%\s*(?:of|от)\s*([\d.]+)", expr, re.IGNORECASE)
    if pct:
        expr = f"{float(pct.group(1)) / 100} * {pct.group(2)}"

    try:
        result = safe_eval(expr)
        if isinstance(result, float) and result == int(result):
            result = int(result)
        return f"Expression: {expr}\nResult: {result}"
    except Exception as e:
        return f"Could not evaluate '{expr}': {e}"
