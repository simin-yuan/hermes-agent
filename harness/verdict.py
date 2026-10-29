import json, sqlite3, sys
D = sys.argv[1]
c = sqlite3.connect(f"{D}/home/state.db")
for sid, model, tcc, end_reason in c.execute("select id, model, tool_call_count, end_reason from sessions"):
    print(f"session={sid[:20]} model={model} tool_call_count={tcc} end_reason={end_reason}")
    for role, content, tool_calls, tool_name, fr in c.execute(
            "select role, content, tool_calls, tool_name, finish_reason from messages where session_id=? order by id", (sid,)):
        tc = json.loads(tool_calls) if tool_calls else None
        args = [t.get("function", {}).get("arguments") for t in tc] if tc else None
        print(f"  {role:9} tool={tool_name or ''} finish={fr or ''} args={json.dumps(args)[:80] if args else ''} content={(content or '')[:90]!r}")
