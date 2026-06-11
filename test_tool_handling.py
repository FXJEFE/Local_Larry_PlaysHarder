"""
test_tool_handling.py - end-to-end check that the local Ollama model can do
native tool handling (function calling) against the project's own local tool.

Flow (the same pattern telegram_bot.py / the MCP client rely on):
  1. Load skills/example_local_tool.py as a callable tool.
  2. Expose it to Ollama as a function schema.
  3. Ask a tool-capable local model a question that requires the tool.
  4. Execute the tool call the model emits, feed the result back.
  5. Confirm the model used the tool and produced a final answer.

Usage:
    python test_tool_handling.py [model]
Defaults to llama3.1:8b (reliable native tool_calls). Pass another model to
test it, e.g.  python test_tool_handling.py LocalLarry-15b
"""
import importlib.util
import json
import os
import re
import sys

import ollama

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "llama3.1:8b"
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_PATH = os.path.join(HERE, "skills", "example_local_tool.py")


def load_local_tool(path):
    spec = importlib.util.spec_from_file_location("example_local_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def coerce_args(args, expected_keys):
    """Normalize the many arg shapes local models emit into {expected_key: value}.

    Local models frequently wrap arguments (e.g. {'input': "{'text': 'x'}"})
    or stringify them, so the tool-handling layer must unwrap before dispatch.
    """
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {expected_keys[0]: args} if expected_keys else {}
    # Unwrap a single generic wrapper key (input/arguments/args/params/kwargs).
    for wrapper in ("input", "arguments", "args", "params", "kwargs"):
        if isinstance(args, dict) and set(args) == {wrapper}:
            inner = args[wrapper]
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner.replace("'", '"'))
                except json.JSONDecodeError:
                    inner = {expected_keys[0]: inner} if expected_keys else {}
            if isinstance(inner, dict):
                args = inner
            break
    # Keep only expected keys; if nothing matched and the tool takes one arg,
    # map the single provided value onto it.
    filtered = {k: v for k, v in args.items() if k in expected_keys}
    if not filtered and len(expected_keys) == 1 and len(args) == 1:
        filtered = {expected_keys[0]: next(iter(args.values()))}
    return filtered


def parse_text_tool_calls(content):
    """Fallback: extract tool calls that a model emitted as TEXT rather than
    native tool_calls (qwen3-coder emits <function=name>...</function>; some
    models emit <tool_call>{json}</tool_call>). Mirrors subagents/base.py.
    """
    calls = []
    for m in re.finditer(r"<function=([\w\-.]+)>(.*?)</function>", content, re.DOTALL):
        name, body = m.group(1), m.group(2).strip()
        args = {}
        try:
            args = json.loads(body)
        except json.JSONDecodeError:
            for pm in re.finditer(r"<parameter=([\w\-]+)>(.*?)</parameter>", body, re.DOTALL):
                args[pm.group(1)] = pm.group(2).strip()
            if not args and body:
                args = {"text": body}
        calls.append({"function": {"name": name, "arguments": args}})
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.DOTALL):
        try:
            obj = json.loads(m.group(1))
            calls.append({"function": {"name": obj.get("name"),
                                       "arguments": obj.get("arguments", {})}})
        except json.JSONDecodeError:
            pass
    return calls


def main():
    tool = load_local_tool(TOOL_PATH)
    print(f"[setup] host={HOST} model={MODEL}")
    print(f"[setup] loaded tool '{os.path.basename(TOOL_PATH)}': {tool.description}")

    # Build the Ollama function schema from the tool's own metadata.
    schema = {
        "type": "function",
        "function": {
            "name": "example_local_tool",
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": tool.parameters,
                "required": ["text"],
            },
        },
    }

    client = ollama.Client(host=HOST)
    messages = [{
        "role": "user",
        "content": "Use the example_local_tool to process the text 'hello from larry' "
                   "and then tell me the processed result and its length.",
    }]

    print("\n[step 1] asking the model (tools offered)...")
    resp = client.chat(model=MODEL, messages=messages, tools=[schema])
    msg = resp["message"]
    calls = msg.get("tool_calls") or []
    mode = "native tool_calls"

    if not calls:
        text_calls = parse_text_tool_calls(msg.get("content") or "")
        if text_calls:
            calls = text_calls
            mode = "TEXT tool calls (parsed fallback)"

    if not calls:
        print("[FAIL] model produced neither native nor text tool calls.")
        print("       content was:\n" + (msg.get("content") or "")[:500])
        return 1

    print(f"[step 2] model requested {len(calls)} tool call(s) via {mode}:")
    messages.append(msg)
    for c in calls:
        fn = c["function"]
        raw = fn["arguments"]
        args = coerce_args(raw, list(tool.parameters.keys()))
        print(f"         -> {fn['name']}(raw={raw}) normalized={args}")
        result = tool.run(**args)
        print(f"         <- {result}")
        messages.append({
            "role": "tool",
            "content": json.dumps(result),
            "tool_name": fn["name"],
        })

    print("\n[step 3] feeding tool result back for the final answer...")
    final = client.chat(model=MODEL, messages=messages)
    answer = final["message"]["content"].strip()
    print("[final answer]\n" + answer)

    ok = "HELLO FROM LARRY" in answer.upper()
    print("\n[PASS] model performed tool handling and used the result."
          if ok else
          "\n[WARN] tool was called, but expected processed text not found in answer.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
