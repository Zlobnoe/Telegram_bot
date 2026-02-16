"""Code runner skill — executes Python in a restricted sandbox."""
from __future__ import annotations

import io
import contextlib

SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
    "chr": chr, "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format, "frozenset": frozenset,
    "hex": hex, "int": int, "isinstance": isinstance, "issubclass": issubclass,
    "iter": iter, "len": len, "list": list, "map": map, "max": max,
    "min": min, "next": next, "oct": oct, "ord": ord, "pow": pow,
    "print": print, "range": range, "repr": repr, "reversed": reversed,
    "round": round, "set": set, "slice": slice, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
}

MAX_OUTPUT = 2000
TIMEOUT_MSG = "Execution timed out (max 5s)"


def execute(query: str, **kwargs) -> str:
    """Run Python code in sandbox."""
    code = query.strip()
    for prefix in ("/run ", "/run\n"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break

    if not code:
        return "No code provided. Usage: /run <python code>"

    stdout = io.StringIO()
    sandbox = {"__builtins__": SAFE_BUILTINS}

    try:
        with contextlib.redirect_stdout(stdout):
            # try as expression first
            try:
                result = eval(code, sandbox)
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                exec(code, sandbox)
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

    output = stdout.getvalue()
    if not output:
        return "Code executed successfully (no output)"
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n... (truncated)"
    return output
