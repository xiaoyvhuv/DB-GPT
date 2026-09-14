"""code_interpreter tool — execute Python code in a subprocess.

Execution boundary: file locations (``FILE_PATH``/``FILES_JSON``/``PLOT_DIR``)
travel only through the subprocess environment; the generated Python source
never interpolates a path literal, so adversarial display names or paths
cannot inject code.
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from dbgpt.agent.resource.tool.base import tool

from .artifact_capture import capture_generated_chart, capture_python_execution

logger = logging.getLogger(__name__)

EXECUTION_TIMEOUT_SECONDS = 60


async def _run_python_file(
    script_path: str,
    *,
    cwd: str,
    env: Optional[Dict[str, str]] = None,
    timeout: int = EXECUTION_TIMEOUT_SECONDS,
) -> Tuple[Optional[int], bytes, bytes]:
    """Run one Python script via ``asyncio.create_subprocess_exec``.

    The process is spawned with an argument list (never a shell string) and
    inherits the parent environment; file locations reach the child only
    through ``env``. Returns ``(returncode, stdout, stderr)``; ``returncode``
    is ``None`` when the run timed out.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        script_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None, b"", b""
    return proc.returncode, stdout, stderr


def build_execution_env(
    *,
    work_dir: str,
    file_path: Optional[str] = None,
    files_json_path: Optional[str] = None,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build the subprocess environment for file-aware code execution."""
    env = dict(os.environ)
    env["PLOT_DIR"] = work_dir
    if file_path:
        env["FILE_PATH"] = file_path
    if files_json_path:
        env["FILES_JSON"] = files_json_path
    for key, value in (extra or {}).items():
        if value is not None:
            env[key] = value
    return env


def _try_repair_truncated_code(raw_code: str) -> Optional[str]:
    """Attempt to fix code that was truncated by the LLM's token limit."""
    lines = raw_code.split("\n")
    for trim in range(1, min(11, len(lines))):
        candidate_lines = lines[: len(lines) - trim]
        if not candidate_lines:
            continue
        candidate = "\n".join(candidate_lines)
        open_chars = {"(": ")", "[": "]", "{": "}"}
        close_chars = set(open_chars.values())
        stack: list = []
        for ch in candidate:
            if ch in open_chars:
                stack.append(open_chars[ch])
            elif ch in close_chars:
                if stack and stack[-1] == ch:
                    stack.pop()
        if stack:
            candidate += "\n" + "".join(reversed(stack))
        try:
            compile(candidate, "<repair>", "exec")
            return candidate
        except SyntaxError:
            continue
    return None


def make_code_interpreter(
    react_state: Dict[str, Any],
    *,
    max_output_chars: int = 50_000,
    legacy_truncation_notice: bool = False,
):
    @tool(
        description=(
            "Execute Python code for data analysis and computation. "
            "Supports pandas, numpy, matplotlib, json, os, etc. "
            "Use this tool when you need to run Python code to process data, "
            "generate charts, or perform calculations. "
            "When SQL was executed earlier in this report turn, use the injected "
            "SQL_RESULTS list or sql_result_frame(index) instead of reconnecting "
            "to or scanning for the database. Each item has artifact_id, sql, "
            "columns, rows, row_count and truncated; index -1 is the latest query. "
            'Parameters: {{"code": "python code string"}}'
        )
    )
    async def code_interpreter(code: str) -> str:
        """Execute arbitrary Python code and return stdout/stderr.

        CRITICAL: Each call is completely independent — variables do NOT
        persist between calls. Every code snippet MUST include all necessary
        data loading and processing. Always print() results you want to see.
        """
        from dbgpt.configs.model_config import PILOT_PATH, STATIC_MESSAGE_IMG_PATH

        if not code or not code.strip():
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": "No code provided"}]},
                ensure_ascii=False,
            )

        cid = react_state.get("conv_id") or "default"
        work_dir = os.path.join(PILOT_PATH, "tmp", cid)
        os.makedirs(work_dir, exist_ok=True)

        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
        pre_existing_images: set = set()
        for root, _dirs, files in os.walk(work_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in IMAGE_EXTS:
                    pre_existing_images.add(os.path.join(root, f))

        preamble_lines = [
            "import json",
            "import os",
            "import pandas as pd",
            "import numpy as np",
            'PLOT_DIR = os.environ["PLOT_DIR"]',
            "os.makedirs(PLOT_DIR, exist_ok=True)",
            'FILE_PATH = os.environ.get("FILE_PATH") or None',
            'FILES_JSON = os.environ.get("FILES_JSON") or None',
            'SQL_RESULTS_PATH = os.environ.get("SQL_RESULTS_PATH") or None',
            "SQL_RESULTS = []",
            "if SQL_RESULTS_PATH:",
            '    with open(SQL_RESULTS_PATH, encoding="utf-8") as _sql_results_file:',
            "        SQL_RESULTS = json.load(_sql_results_file)",
            'def sql_result_frame(index=-1):',
            '    dataset = SQL_RESULTS[index]',
            '    return pd.DataFrame(dataset["rows"], columns=dataset["columns"])',
        ]
        preamble = "\n".join(preamble_lines) + "\n"
        full_code = preamble + code
        started = time.perf_counter()

        try:
            compile(full_code, "<code_interpreter>", "exec")
        except SyntaxError as se:
            repaired = _try_repair_truncated_code(full_code)
            if repaired is not None:
                logger.warning(
                    "code_interpreter: auto-repaired truncated code "
                    "(original SyntaxError: %s line %s)",
                    se.msg,
                    se.lineno,
                )
                full_code = repaired
                code = full_code[len(preamble) :]
            else:
                error_msg = (
                    f"SyntaxError before execution: {se.msg} "
                    f"(line {se.lineno})\n"
                    "Please regenerate complete, syntactically valid Python code. "
                    "Keep code under 80 lines."
                )
                capture_python_execution(
                    code=code,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    status="failed",
                    exit_code=None,
                    stdout="",
                    stderr="",
                    error=error_msg,
                    artifact_sink=react_state.get("_report_artifact_sink"),
                )
                return json.dumps(
                    {
                        "chunks": [
                            {"output_type": "code", "content": code.strip()},
                            {"output_type": "text", "content": error_msg},
                        ]
                    },
                    ensure_ascii=False,
                )

        output_text = ""
        error_text = ""
        returncode: Optional[int] = None
        execution_error: Optional[str] = None
        stdout = b""
        stderr = b""
        stdout_text = ""
        sql_input_artifact_ids: List[str] = []
        try:
            sql_results_path = None
            sql_results = react_state.get("_report_sql_results")
            if isinstance(sql_results, list) and sql_results:
                sql_results_path = os.path.join(work_dir, "_report_sql_results.json")
                with open(sql_results_path, "w", encoding="utf-8") as context_file:
                    json.dump(sql_results, context_file, ensure_ascii=False, allow_nan=False)
                # Record an actual lineage edge only when the submitted code
                # consumes the injected SQL context.  Merely having earlier
                # query results available must not claim a false dependency.
                if "sql_result_frame" in code or "SQL_RESULTS" in code:
                    sql_input_artifact_ids = [
                        item["artifact_id"]
                        for item in sql_results
                        if isinstance(item, dict)
                        and isinstance(item.get("artifact_id"), str)
                    ]
            tmp_path = os.path.join(work_dir, "_run.py")
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                tmp.write(full_code)

            returncode, stdout, stderr = await _run_python_file(
                tmp_path,
                cwd=work_dir,
                env=build_execution_env(
                    work_dir=work_dir,
                    file_path=react_state.get("file_path"),
                    files_json_path=react_state.get("files_json_path"),
                    extra={"SQL_RESULTS_PATH": sql_results_path} if sql_results_path else None,
                ),
                timeout=EXECUTION_TIMEOUT_SECONDS,
            )
            stdout_text = stdout.decode("utf-8", errors="replace")
            output_text = stdout_text
            error_text = stderr.decode("utf-8", errors="replace")

            if returncode is None:
                # ``_run_python_file`` returns ``None`` only on timeout.
                output_text = (
                    f"Execution timed out ({EXECUTION_TIMEOUT_SECONDS}s limit)"
                )
            elif returncode and error_text:
                output_text = (
                    output_text + "\n[ERROR]\n" + error_text
                    if output_text
                    else error_text
                )
        except Exception as e:
            execution_error = f"Execution error: {e}"
            output_text = execution_error
        execution_duration_ms = int((time.perf_counter() - started) * 1000)

        chunks: List[Dict[str, Any]] = [
            {"output_type": "code", "content": code.strip()},
        ]
        if output_text.strip():
            clean_output = output_text.strip()
            # Raised from 2000 to 50_000: data-analysis tasks frequently need
            # more than 2000 chars of output (e.g. DataFrame summaries). Larger
            # outputs are persisted to disk by the ToolResultStorage layer
            # rather than truncated here, so the agent can read_file to recover
            # the full content.
            max_out_len = max_output_chars
            if len(clean_output) > max_out_len:
                if legacy_truncation_notice:
                    truncation_notice = (
                        f"\n\n... [Output truncated, length: {len(clean_output)} chars."
                        f" Only showing first {max_out_len} chars."
                        " If you generated HTML, the file is saved.]"
                    )
                else:
                    truncation_notice = (
                        f"\n\n... [Output truncated, length: {len(clean_output)} chars."
                        f" Only showing first {max_out_len} chars.]"
                    )
                clean_output = clean_output[:max_out_len] + truncation_notice
            chunks.append({"output_type": "text", "content": clean_output})
        else:
            chunks.append(
                {
                    "output_type": "text",
                    "content": "(no output — add print() to see results)",
                }
            )

        # Scan for new images generated by this run
        captured_images: List[Tuple[str, str]] = []
        try:
            os.makedirs(STATIC_MESSAGE_IMG_PATH, exist_ok=True)
            for root, _dirs, files in os.walk(work_dir):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    full_path = os.path.join(root, fname)
                    if ext in IMAGE_EXTS and full_path not in pre_existing_images:
                        unique_name = f"{uuid.uuid4().hex[:8]}_{fname}"
                        dest = os.path.join(STATIC_MESSAGE_IMG_PATH, unique_name)
                        shutil.copy2(full_path, dest)
                        img_url = f"/images/{unique_name}"
                        chunks.append({"output_type": "image", "content": img_url})
                        react_state.setdefault("generated_images", []).append(img_url)
                        captured_images.append((img_url, dest))
        except Exception:
            pass

        if execution_error is not None:
            status = "failed"
            artifact_error = execution_error
        elif returncode is None:
            status = "timed_out"
            artifact_error = output_text
        elif returncode != 0:
            status = "failed"
            artifact_error = (
                error_text.strip() or f"Python exited with code {returncode}"
            )
        else:
            status = "succeeded"
            artifact_error = None

        python_artifact_id = capture_python_execution(
            code=code,
            duration_ms=execution_duration_ms,
            status=status,
            exit_code=returncode,
            stdout=stdout_text,
            stderr=error_text,
            error=artifact_error,
            image_urls=[url for url, _path in captured_images],
            input_artifact_ids=sql_input_artifact_ids,
            artifact_sink=react_state.get("_report_artifact_sink"),
        )
        if python_artifact_id is not None:
            for image_url, image_path in captured_images:
                try:
                    capture_generated_chart(
                        source_artifact_id=python_artifact_id,
                        uri=image_url,
                        path=image_path,
                        filename=os.path.basename(image_path),
                        artifact_sink=react_state.get("_report_artifact_sink"),
                    )
                except Exception:
                    logger.warning(
                        "Unable to read generated image for report capture",
                        exc_info=True,
                    )

        # Clean up temp script
        try:
            script_path = os.path.join(work_dir, "_run.py")
            if os.path.exists(script_path):
                os.remove(script_path)
            if 'sql_results_path' in locals() and sql_results_path and os.path.exists(sql_results_path):
                os.remove(sql_results_path)
        except Exception:
            pass

        all_images = react_state.get("generated_images", [])
        if all_images:
            img_summary = "已生成的图片URL（在生成HTML时请使用这些URL）:\n" + "\n".join(
                f"  - {url}" for url in all_images
            )
            chunks.append({"output_type": "text", "content": img_summary})

        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    return code_interpreter
