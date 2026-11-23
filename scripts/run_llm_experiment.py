#!/usr/bin/env python3
"""Real-model rerun: one model answers every question under both conditions.

Condition llm_baseline: the model sees the question text and the DuckDB schema.
Condition llm_grounded: the model sees the question text, the fund definitions
file, and the same schema. The structured task block in questions.yml is never
sent. The model writes one SQL query; the harness runs it locally, read-only,
on the fictional ledgers and scores the result against results/ground_truth.json.

The API key is read from the NVIDIA_API_KEY environment variable and is only
placed in the Authorization header. It is never written to disk or printed.
Only prompts made from the fictional schema, definitions and question text are
sent to the model endpoint. KumoRFM is not used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pe_metrics.db import connect  # noqa: E402
from pe_metrics.definitions import DEFINITIONS_PATH, load_definitions, load_questions  # noqa: E402
from pe_metrics.generate import generate_portfolio  # noqa: E402
from pe_metrics import llm_eval  # noqa: E402

ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
LOCK = threading.Lock()
CHECKPOINT: Path | None = None


def call_model(key: str, model: str, messages: list[dict], max_tokens: int, timeout: int) -> dict:
    """Stream one chat completion with a hard wall-clock deadline per attempt.

    Streaming lets the harness abandon a request that is still generating after
    ``timeout`` seconds, rather than waiting on a socket that never goes idle.
    Only the final answer text is kept; any reasoning stream is discarded.
    """
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode()
    last = None
    for attempt in range(3):
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        started = time.time()
        content, finish, returned, usage = [], None, None, None
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw in response:
                    if time.time() - started > timeout:
                        raise TimeoutError(f"no complete answer after {timeout} s")
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    returned = chunk.get("model") or returned
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            content.append(piece)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
            return {
                "ok": True,
                "content": "".join(content),
                "finish_reason": finish,
                "model_returned": returned,
                "usage": usage,
                "seconds": round(time.time() - started, 1),
                "http_attempts": attempt + 1,
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace").replace(key, "<redacted>")
            last = f"HTTP {exc.code}: {detail}"
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except Exception as exc:  # timeouts, resets
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
        time.sleep(5 * (attempt + 1))
    return {"ok": False, "error": last}


def run_one(key, args, cursor_factory, names, definitions, question, truth, condition, repeat, schema, defs_text):
    messages = llm_eval.build_messages(condition, question["text"], schema, defs_text)
    turns = []
    status, value, sql, notes, executed = None, None, None, None, None
    cursor = cursor_factory()
    for turn in range(2):  # first answer plus at most one repair
        reply = call_model(key, args.model, messages, args.max_tokens, args.timeout)
        record = {k: reply.get(k) for k in ("ok", "finish_reason", "model_returned", "usage", "seconds", "error")}
        record["content"] = reply.get("content")
        turns.append(record)
        if not reply["ok"]:
            status = "api_error"
            break
        parsed = llm_eval.parse_reply(reply.get("content"))
        if not parsed["ok"]:
            status = "format_error"
            if turn == 0:
                messages = messages + [
                    {"role": "assistant", "content": reply.get("content") or ""},
                    {"role": "user", "content": "Reply with only the JSON object described in the rules."},
                ]
                continue
            break
        sql, notes = parsed["sql"], parsed["notes"]
        if not parsed["answerable"] or not sql:
            status = "unanswerable"
            sql = None
            break
        executed = llm_eval.run_sql(cursor, sql)
        if executed["ok"]:
            status = "answered"
            break
        status = "sql_error"
        if turn == 0:
            messages = messages + [
                {"role": "assistant", "content": reply.get("content") or ""},
                llm_eval.repair_message(executed["error"]),
            ]
    cursor.close()
    normalise_error = None
    if status == "answered":
        value, normalise_error = llm_eval.normalise(executed["raw"], truth["value_kind"], names) \
            if truth["value_kind"] != "unanswerable" else (str(executed["raw"]), None)
    if status == "answered" and value is None:
        status = "null_answer"  # the query ran but returned NULL
    attempt = {"status": status, "value": value}
    correct = llm_eval.score(truth, attempt) if status in ("answered", "unanswerable") else False
    guard = llm_eval.guard_check(question, definitions, sql if status == "answered" else None)
    gated = correct and (guard.get("passed") is not False)
    row = {
        "model": args.model,
        "condition": condition,
        "repeat": repeat,
        "id": question["id"],
        "question": question["text"],
        "status": status,
        "sql": sql,
        "raw_result": executed.get("raw") if executed and executed.get("ok") else None,
        "sql_error": executed.get("error") if executed and not executed.get("ok") else None,
        "value": value,
        "normalise_error": normalise_error,
        "ground_truth": truth["value"],
        "ground_truth_display": truth["display"],
        "value_kind": truth["value_kind"],
        "correct": correct,
        "abs_error": llm_eval.abs_error(truth, attempt),
        "guard": guard,
        "correct_if_guard_blocks": gated,
        "notes": notes,
        "turns": turns,
    }
    with LOCK:
        print(f"{condition} r{repeat} {question['id']} {status} {'OK' if correct else 'x'}", flush=True)
        if CHECKPOINT is not None:
            with CHECKPOINT.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--conditions", default=",".join(llm_eval.CONDITIONS))
    parser.add_argument("--questions", default="")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "duckdb")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        raise SystemExit("set NVIDIA_API_KEY in the environment")

    definitions = load_definitions()
    questions = load_questions(definitions=definitions)
    if args.questions:
        wanted = set(args.questions.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    truth = {row["question_id"]: row for row in json.loads((ROOT / "results" / "ground_truth.json").read_text())}
    paths = generate_portfolio(args.data_dir, definitions)
    connection = connect(paths)
    schema = llm_eval.schema_text(connection)
    defs_text = llm_eval.definitions_text(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    names = {code: spec["legal_name"] for code, spec in definitions["entities"].items()}
    global CHECKPOINT
    CHECKPOINT = args.out.with_suffix(".partial.jsonl")
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["status"] != "api_error":
                done[(row["condition"], row["repeat"], row["id"])] = row
    started = datetime.now(timezone.utc)
    jobs = [
        (condition, repeat, question)
        for repeat in range(1, args.repeats + 1)
        for condition in args.conditions.split(",")
        for question in questions
        if (condition, repeat, question["id"]) not in done
    ]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                run_one, key, args, connection.cursor, names, definitions, q, truth[q["id"]],
                c, r, schema, defs_text,
            )
            for c, r, q in jobs
        ]
        rows = list(done.values()) + [f.result() for f in futures]
    rows.sort(key=lambda r: (r["condition"], r["repeat"], r["id"]))
    connection.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "model": args.model,
                "endpoint": ENDPOINT,
                "temperature": 0,
                "max_tokens": args.max_tokens,
                "repeats": args.repeats,
                "started_utc": started.isoformat(timespec="seconds"),
                "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "prompt": {
                    "system_rules": llm_eval.SYSTEM_RULES,
                    "schema_chars": len(schema),
                    "definitions_chars": len(defs_text),
                    "task_block_sent": False,
                },
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("wrote", args.out)


if __name__ == "__main__":
    main()
