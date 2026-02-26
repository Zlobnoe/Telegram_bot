"""Code runner skill — executes Python in a restricted sandbox."""
from __future__ import annotations

import io
import contextlib
import threading

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
TIMEOUT_SECONDS = 5
TIMEOUT_MSG = f"Execution timed out (max {TIMEOUT_SECONDS}s)"


def _run_in_sandbox(code: str) -> str:
    stdout = io.StringIO()
    sandbox = {"__builtins__": SAFE_BUILTINS}
    try:
        with contextlib.redirect_stdout(stdout):
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


def execute(query: str, **kwargs) -> str:
    """Run Python code in sandbox with a hard 5-second timeout."""
    code = query.strip()
    for prefix in ("/run ", "/run\n"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break

    if not code:
        return "No code provided. Usage: /run <python code>"

    result: list[str] = []

    def _run() -> None:
        result.append(_run_in_sandbox(code))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=TIMEOUT_SECONDS)

    if thread.is_alive():
        return TIMEOUT_MSG
    return result[0] if result else "Code executed successfully (no output)"
