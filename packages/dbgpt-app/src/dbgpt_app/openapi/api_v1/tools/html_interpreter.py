"""html_interpreter tool — render HTML as an interactive report."""

import hashlib
import json
import logging
import os
import re
from html import unescape as unescape_html
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dbgpt.agent.resource.tool.base import tool

logger = logging.getLogger(__name__)


def _validate_complete_html_document(html: str) -> str | None:
    """Reject partial HTML instead of persisting a misleading rendered report.

    A ReAct response can be cut off while it is still writing the ``html``
    argument.  Browsers make a best effort to display that fragment, which used
    to let ``html_interpreter`` announce success even though the stored report
    was missing the end of a chart/card (and often the closing document tags).
    Root/body closing tags are a deliberately small, deterministic completeness
    gate that does not attempt to reimplement an HTML parser.
    """
    normalized = html.strip().lower()
    if not re.search(r"<html(?:\s|>)", normalized):
        return (
            "HTML_DOCUMENT_INCOMPLETE: Pass one complete HTML document beginning "
            "with <html> and ending with </body></html>. Retry html_interpreter "
            "with the complete document; do not rerun successful analysis tools."
        )
    if not re.search(r"<body(?:\s|>)", normalized) or not re.search(
        r"</body\s*>", normalized
    ) or not re.search(r"</html\s*>\s*$", normalized):
        return (
            "HTML_DOCUMENT_INCOMPLETE: The html argument was truncated or is "
            "missing </body></html>. Retry html_interpreter with the complete "
            "document and keep the existing real artifact bindings."
        )
    return None


def _validate_recommendation_requirement(
    html: str, required_count: int | None
) -> str | None:
    """Ensure an explicitly requested recommendation count reaches the report.

    This is intentionally a narrow delivery-completeness check, not a content
    quality scorer.  The model may choose the recommendation wording and style,
    but a user who explicitly asks for a fixed number of actionable
    recommendations must not receive a persisted report that silently omits
    that requested section.
    """
    if not isinstance(required_count, int) or required_count < 1:
        return None

    headings = list(
        re.finditer(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]\s*>", html, re.IGNORECASE | re.DOTALL)
    )
    for index, heading in enumerate(headings):
        heading_text = re.sub(r"<[^>]+>", " ", heading.group(1))
        heading_text = " ".join(unescape_html(heading_text).split()).lower()
        if "建议" not in heading_text and "recommend" not in heading_text:
            continue
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(html)
        section = html[heading.end() : section_end]
        items = re.findall(r"<li\b[^>]*>(.*?)</li\s*>", section, re.IGNORECASE | re.DOTALL)
        actionable_items = [
            " ".join(unescape_html(re.sub(r"<[^>]+>", " ", item)).split())
            for item in items
        ]
        if len([item for item in actionable_items if item]) >= required_count:
            return None

    return (
        "REPORT_REQUIREMENT_MISSING: The user explicitly requested "
        f"{required_count} actionable recommendation(s), but the HTML does not "
        "contain that many items under a recommendation section. Add a visible "
        "<h2>经营建议</h2><ol><li>...</li></ol> section with the requested "
        "number of concrete actions, bind any numeric claims to their actual "
        "evidence, and retry html_interpreter without rerunning completed analysis."
    )


def _validate_evidence_bindings(
    html: str,
    artifact_ids: List[str],
    required_artifact_ids: List[str] | None = None,
    required_native_chart_artifact_ids: List[str] | None = None,
    artifact_types: Dict[str, str] | None = None,
) -> str | None:
    """Return a retry message when a data-backed report has no valid bindings."""
    available_ids = {str(value).strip() for value in artifact_ids if str(value).strip()}
    if not available_ids:
        return None

    bindings = re.findall(
        r"\bdata-dbgpt-artifacts\s*=\s*['\"]([^'\"]+)['\"]", html, re.IGNORECASE
    )
    if not bindings:
        return (
            "TRACEABILITY_BINDING_REQUIRED: This report uses executed artifacts but "
            "contains no object-level evidence binding. Call report_evidence_manifest "
            "and retry html_interpreter. Put real artifact IDs on EACH KPI/card/chart "
            "container and wrap EACH numeric claim in a conclusion with a separate "
            "<strong data-dbgpt-artifacts=\"...\"> value </strong>. Do not bind all "
            "artifacts to every element."
        )

    referenced_ids = {
        item.strip()
        for binding in bindings
        for item in binding.split(",")
        if item.strip()
    }
    unknown_ids = referenced_ids - available_ids
    if unknown_ids:
        return (
            "TRACEABILITY_BINDING_INVALID: The HTML references artifact IDs not found "
            f"in this run: {', '.join(sorted(unknown_ids)[:10])}. Call "
            "report_evidence_manifest again and retry with only its real IDs."
        )
    # A table is already a data presentation.  Binding a ChartArtifact to it
    # makes the UI advertise a chart where there is none ("SQL · chart"), and
    # makes the evidence drawer misleading.  A chart must be bound to the
    # actual figure/image/chart container that renders it instead.  This is a
    # semantic constraint only: it does not alter DB-GPT's generated markup or
    # report styling.
    chart_ids = {
        artifact_id
        for artifact_id, artifact_type in (artifact_types or {}).items()
        if artifact_type == "chart"
    }
    for match in _EVIDENCE_TAG_PATTERN.finditer(html):
        if match.group("tag").lower() != "table":
            continue
        bound_chart_ids = {
            item.strip()
            for item in match.group("ids").split(",")
            if item.strip() in chart_ids
        }
        if bound_chart_ids:
            return (
                "TRACEABILITY_BINDING_INVALID: A <table> is bound to chart "
                f"artifact(s) {', '.join(sorted(bound_chart_ids))}, but it does not "
                "render those charts. Keep only the SQL/Python evidence supporting "
                "the table on <table>; put each ChartArtifact on the separate "
                "<figure>, chart container, or generated <img> that displays it."
            )
    required_ids = set(required_artifact_ids or [])
    missing_required = required_ids - referenced_ids
    if missing_required:
        return (
            "TRACEABILITY_BINDING_REQUIRED: The report embeds generated chart output "
            "but does not cite its chart artifact. Bind the chart image or its "
            "containing figure/section to the real artifact IDs: "
            f"{', '.join(sorted(missing_required))}. Also retain the supporting SQL "
            "artifact on that chart container."
        )
    required_native_chart_ids = set(required_native_chart_artifact_ids or [])
    if required_native_chart_ids:
        figure_chart_ids = {
            item.strip()
            for match in _EVIDENCE_TAG_PATTERN.finditer(html)
            if match.group("tag").lower() == "figure"
            for item in match.group("ids").split(",")
            if item.strip()
        }
        missing_native_charts = required_native_chart_ids - figure_chart_ids
        if missing_native_charts:
            return (
                "TRACEABILITY_BINDING_REQUIRED: This run generated native chart "
                "artifact(s) that are not rendered in the report: "
                f"{', '.join(sorted(missing_native_charts))}. Add one explicit empty "
                "<figure data-dbgpt-artifacts=\"supporting-sql,chart-id\" "
                "data-dbgpt-evidence-label=\"chart title\"></figure> for each "
                "listed chart. Do not bind chart IDs to a <table>; DB-GPT mounts "
                "native charts only on their own figure containers."
            )
    return None


_EVIDENCE_TAG_PATTERN = re.compile(
    r"<(?P<tag>[a-zA-Z][\w:-]*)\b(?P<attrs>[^>]*\bdata-dbgpt-artifacts\s*=\s*"
    r"(?P<quote>['\"])(?P<ids>[^'\"]+)(?P=quote)[^>]*)>",
    re.IGNORECASE,
)
_INLINE_EVIDENCE_TAGS = {"strong", "b", "span", "em", "mark", "a"}


def _has_non_image_evidence_binding(html: str) -> bool:
    """An auto-bound image must not make an otherwise unbound report valid."""
    return any(
        match.group("tag").lower() != "img"
        for match in _EVIDENCE_TAG_PATTERN.finditer(html)
    )


def _bind_generated_chart_images(html: str, artifacts: List[Any]) -> str:
    """Give each known generated image its own chart/Python lineage binding.

    This is deliberately limited to an image whose URL exactly identifies a
    captured ChartArtifact.  It neither assigns SQL evidence to unrelated
    content nor replaces the model's required KPI/claim bindings.
    """
    by_id = {getattr(artifact, "artifact_id", ""): artifact for artifact in artifacts}
    charts = {}
    for artifact in artifacts:
        if getattr(artifact, "type", "") != "chart":
            continue
        output = getattr(artifact, "output", None)
        uri = getattr(output, "uri", None) if output is not None else None
        if uri:
            charts[os.path.basename(str(uri).split("?", 1)[0])] = artifact

    def lineage(artifact_id: str, seen: set[str] | None = None) -> List[str]:
        seen = seen or set()
        if artifact_id in seen:
            return []
        seen.add(artifact_id)
        artifact = by_id.get(artifact_id)
        if artifact is None:
            return []
        values: List[str] = []
        for input_id in getattr(artifact, "input_artifact_ids", []) or []:
            values.extend(lineage(input_id, seen))
        values.append(artifact_id)
        return values

    def annotate(match: re.Match) -> str:
        attrs = match.group("attrs")
        src = re.search(r"\bsrc\s*=\s*(['\"])(.*?)\1", attrs, re.IGNORECASE)
        if not src:
            return match.group(0)
        chart = charts.get(os.path.basename(src.group(2).split("?", 1)[0]))
        if chart is None:
            return match.group(0)
        ids = list(dict.fromkeys(lineage(chart.artifact_id)))
        existing = re.search(
            r"\bdata-dbgpt-artifacts\s*=\s*(['\"])([^'\"]+)\1",
            attrs,
            re.IGNORECASE,
        )
        if existing:
            merged = list(
                dict.fromkeys(
                    [
                        *(item.strip() for item in existing.group(2).split(",")),
                        *ids,
                    ]
                )
            )
            attrs = attrs[: existing.start(2)] + ",".join(merged) + attrs[existing.end(2) :]
        else:
            attrs += (
                f' data-dbgpt-artifacts="{",".join(ids)}"'
                ' data-dbgpt-evidence-label="生成图表"'
            )
        return f"<img{attrs}>"

    return re.sub(r"<img\b(?P<attrs>[^>]*)>", annotate, html, flags=re.IGNORECASE)


def _prepare_presentation_bindings(
    html: str,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Assign stable HTML target IDs and return their structured bindings."""
    bindings: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _attribute(attrs: str, name: str) -> str | None:
        match = re.search(
            rf"\b{re.escape(name)}\s*=\s*(['\"])(.*?)\1",
            attrs,
            re.IGNORECASE | re.DOTALL,
        )
        return unescape_html(match.group(2).strip()) if match else None

    def _bind(match: re.Match) -> str:
        tag = match.group("tag")
        attrs = match.group("attrs")
        if re.search(r"\bdata-dbgpt-evidence-summary\b", attrs, re.IGNORECASE):
            return match.group(0)
        explicit_kind = (_attribute(attrs, "data-dbgpt-target-kind") or "").lower()
        target_type = (
            "claim"
            if explicit_kind == "claim" or tag.lower() in _INLINE_EVIDENCE_TAGS
            else "block"
        )
        id_attribute = (
            "data-dbgpt-claim-id" if target_type == "claim" else "data-dbgpt-block-id"
        )
        target_id = _attribute(attrs, id_attribute)
        if (
            not target_id
            or len(target_id) > 100
            or not re.fullmatch(r"[A-Za-z0-9_.:-]+", target_id)
            or target_id in seen_ids
        ):
            target_id = f"html-{target_type}-{len(bindings) + 1}"
            attrs += f' {id_attribute}="{target_id}"'
        seen_ids.add(target_id)
        artifact_ids = [
            value.strip() for value in match.group("ids").split(",") if value.strip()
        ]
        label = _attribute(attrs, "data-dbgpt-evidence-label") or target_id
        bindings.append(
            {
                "target_id": target_id,
                "target_type": target_type,
                "label": label[:512],
                "artifact_ids": list(dict.fromkeys(artifact_ids)),
            }
        )
        return f"<{tag}{attrs}>"

    prepared_html = _EVIDENCE_TAG_PATTERN.sub(_bind, html)
    for binding in bindings:
        if binding["label"] != binding["target_id"]:
            continue
        id_attribute = (
            "data-dbgpt-claim-id"
            if binding["target_type"] == "claim"
            else "data-dbgpt-block-id"
        )
        content_match = re.search(
            rf"<(?P<tag>[a-zA-Z][\w:-]*)\b[^>]*\b{id_attribute}\s*=\s*"
            rf"(['\"]){re.escape(binding['target_id'])}\2[^>]*>"
            rf"(?P<body>.*?)</(?P=tag)\s*>",
            prepared_html,
            re.IGNORECASE | re.DOTALL,
        )
        if content_match:
            content = re.sub(r"<[^>]+>", " ", content_match.group("body"))
            content = " ".join(unescape_html(content).split())
            if content:
                binding["label"] = content[:512]
    return prepared_html, bindings


def make_html_interpreter(react_state: Dict[str, Any], skills_dir: str):
    @tool(
        description=(
            "将 HTML 渲染为可交互的网页报告，这是向用户展示网页报告的唯一方式。"
            "【一次性】一次调用即可把【完整】报告渲染出来；同一份报告【禁止】"
            "重复调用本工具，渲染成功后若目标已达成请直接 terminate。"
            "【默认用法】直接传入完整的 HTML 字符串："
            '{"html": "<html>...</html>", "title": "报告标题"}。'
            "你需要自己生成完整的 HTML 代码"
            "（包含 <!DOCTYPE html>、<html>、<head>、<body> 等），"
            "然后传给 html 参数即可。"
            "HTML 可以很长，没有长度限制，不需要分段传入；"
            "若报告含多部分内容，请合并进【同一份】HTML 一次性渲染，"
            "不要分多次生成多份报告。"
            "【可追溯报告】生成分析报告前，必须先调用 report_evidence_manifest。"
            "必须做【对象级】绑定：每个 KPI 卡、图表/表格容器分别写入仅支持它的 "
            "artifact_id，例如 <section "
            "data-dbgpt-artifacts=\"sql-gmv,python-chart\">。"
            "每个绑定元素还要提供简洁的 data-dbgpt-evidence-label，"
            "说明它对应的指标或结论。"
            "结论中每一个引用数据的数值必须加粗并单独可追溯，例如 "
            "<strong data-dbgpt-artifacts=\"sql-gmv\">R$13.4M</strong>。"
            "知识库、网页或文件证据也必须绑定对应 reference artifact_id。"
            "不可把全部 artifact_id 笼统绑定到每个元素；没有真实证据时不要添加该属性。"
            "存在执行产物却没有有效对象级绑定时，本工具会拒绝渲染并要求你修正。"
            "【禁止】不要用 code_interpreter 写 HTML 再 print，"
            "不要用 code_interpreter 把 HTML 写入文件再读取，"
            "直接把 HTML 传给本工具即可。"
            "【技能模式 - 仅在使用技能时可选】"
            "如果正在使用技能（skill），可以用模板模式："
            '{"template_path": "技能名/templates/模板.html", '
            '"data": {"KEY": "值"}, "title": "标题"}。'
            '也可以用文件模式：{"file_path": "/path/to/report.html"}'
        )
    )
    async def html_interpreter(
        html: str = "",
        title: str = "Report",
        file_path: str = "",
        template_path: str = "",
        data: dict | str = None,
    ) -> str:
        """Render HTML as an interactive web report."""
        from dbgpt.configs.model_config import STATIC_MESSAGE_IMG_PATH

        # Keep terminal handling honest: tool validation failures must not later
        # be exposed as a completed HTML report.
        react_state["_report_html_attempted"] = True
        react_state["_report_html_error"] = "HTML report rendering did not complete."
        skills_path = Path(skills_dir).expanduser().resolve()

        # ── Mode 1: template_path + data ──
        if template_path and template_path.strip():
            tp = template_path.strip()
            target = (skills_path / tp).resolve()
            try:
                target.relative_to(skills_path)
            except ValueError:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Invalid template_path: {tp}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if not target.is_file():
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Template not found: {tp}. "
                                    "Please retry using the `html` parameter "
                                    "directly — "
                                    "generate complete HTML yourself and pass it via "
                                    '{"html": "<html>...</html>", "title": "title"}.'
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            try:
                raw_template = target.read_text(encoding="utf-8")
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading template: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

            replacements = data
            if isinstance(replacements, str):
                try:
                    replacements = json.loads(replacements)
                except Exception:
                    try:
                        fixed = str(replacements).rstrip()
                        if not fixed.endswith("}"):
                            fixed += '"}' if not fixed.endswith('"') else "}"
                        replacements = json.loads(fixed)
                    except Exception:
                        replacements = {}
            if not isinstance(replacements, dict):
                replacements = {}

            auto_data = react_state.get("auto_data", {})
            if isinstance(auto_data, dict):
                replacements = {**auto_data, **replacements}

            ratio_data = react_state.get("ratio_data", {})
            if isinstance(ratio_data, dict):
                replacements = {**ratio_data, **replacements}

            image_url_map = react_state.get("image_url_map", {})
            if isinstance(image_url_map, dict):
                for stem, url in image_url_map.items():
                    chart_key = f"CHART_{stem.upper()}"
                    if chart_key not in replacements:
                        replacements[chart_key] = url

            def _replace_placeholder(m):
                key = m.group(1)
                return str(replacements.get(key, ""))

            html = re.sub(r"\{\{([A-Z_0-9]+)\}\}", _replace_placeholder, raw_template)
            if not title or title == "Report":
                title = target.stem

        # ── Mode 2: file_path ──
        elif file_path and file_path.strip():
            fp = file_path.strip()
            if not os.path.isfile(fp):
                cid = react_state.get("conv_id") or "default"
                from dbgpt.configs.model_config import PILOT_PATH

                alt = os.path.join(PILOT_PATH, "data", cid, os.path.basename(fp))
                if os.path.isfile(alt):
                    fp = alt
                else:
                    return json.dumps(
                        {
                            "chunks": [
                                {
                                    "output_type": "text",
                                    "content": f"File not found: {file_path}",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    html = f.read()
                if not title or title == "Report":
                    title = os.path.splitext(os.path.basename(fp))[0]
            except Exception as e:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Error reading file: {e}",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        # ── Mode 3: inline html ──
        if html and isinstance(html, str) and not template_path and not file_path:
            if "\\n" in html:
                html = html.replace("\\n", "\n")
            if "\\t" in html:
                html = html.replace("\\t", "\t")

        if not html or not html.strip():
            return json.dumps(
                {
                    "chunks": [
                        {"output_type": "text", "content": "No HTML content provided"}
                    ]
                },
                ensure_ascii=False,
            )

        document_error = _validate_complete_html_document(html)
        if document_error:
            react_state["_report_html_error"] = document_error
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": document_error}]},
                ensure_ascii=False,
            )

        # Post-process: fix image URLs
        fixed_html = html.strip()
        try:
            IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
            name_to_served: Dict[str, str] = {}
            if os.path.isdir(STATIC_MESSAGE_IMG_PATH):
                for fname in os.listdir(STATIC_MESSAGE_IMG_PATH):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in IMAGE_EXTS:
                        continue
                    m = re.match(r"^[0-9a-f]{8}_(.+)$", fname, re.IGNORECASE)
                    if m:
                        name_to_served[m.group(1).lower()] = f"/images/{fname}"

            if name_to_served:

                def _fix_img_src(match: re.Match) -> str:
                    prefix = match.group(1)
                    raw_path = match.group(2)
                    quote = match.group(3)
                    filename = raw_path.rsplit("/", 1)[-1].lower()
                    if re.match(r"^[0-9a-f]{8}_.+$", filename, re.IGNORECASE):
                        return match.group(0)
                    if filename in name_to_served:
                        return f"{prefix}{name_to_served[filename]}{quote}"
                    return match.group(0)

                fixed_html = re.sub(
                    r"""(src\s*=\s*["'])([^"']+\.(?:png|jpg|jpeg|gif|svg|webp))(["'])""",
                    _fix_img_src,
                    fixed_html,
                    flags=re.IGNORECASE,
                )
        except Exception:
            pass

        # Auto-append missing images
        try:
            gen_images = react_state.get("generated_images", [])
            if gen_images:
                html_img_stems = set(
                    re.sub(r"^[0-9a-f]+_", "", os.path.basename(src))
                    for src in re.findall(
                        r'<img[^>]+src=["\']([^"\']+)["\']', fixed_html, re.IGNORECASE
                    )
                )

                def _img_stem(url):
                    return re.sub(r"^[0-9a-f]+_", "", os.path.basename(url))

                missing = [
                    url
                    for url in gen_images
                    if url not in fixed_html and _img_stem(url) not in html_img_stems
                ]
                if missing:
                    imgs_html = "".join(
                        f'<div style="margin:16px 0">'
                        f'<img src="{url}" '
                        f'style="max-width:100%;height:auto;border-radius:8px">'
                        f"</div>"
                        for url in missing
                    )
                    section = (
                        '<div style="margin-top:32px"><h2>📊 分析图表</h2>'
                        f"{imgs_html}</div>"
                    )
                    if "</body>" in fixed_html.lower():
                        fixed_html = re.sub(
                            r"(</body>)",
                            section + r"\1",
                            fixed_html,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        fixed_html += section
        except Exception:
            pass

        requirement_error = _validate_recommendation_requirement(
            fixed_html, react_state.get("_report_required_recommendation_count")
        )
        if requirement_error:
            react_state["_report_html_error"] = requirement_error
            return json.dumps(
                {"chunks": [{"output_type": "text", "content": requirement_error}]},
                ensure_ascii=False,
            )

        # A model may finish a correct analysis yet omit the HTML attributes requested
        # by the traceability protocol.  Bind the actual artifacts captured in this
        # ReAct run as a transparent report-level fallback, rather than emitting an
        # attractive but untraceable HTML report.
        try:
            artifact_sink = react_state.get("_report_artifact_sink")
            artifacts = artifact_sink.snapshot() if artifact_sink is not None else []
            successful_artifact_ids = [
                artifact.artifact_id
                for artifact in artifacts
                if getattr(artifact, "status", "") == "succeeded"
            ]
            # A generated image is a concrete chart output.  If that image is
            # embedded in the report, it must expose its own chart artifact in
            # addition to the SQL data artifact; otherwise the Python/chart
            # provenance would disappear behind an apparently traceable figure.
            required_chart_artifact_ids = []
            for artifact in artifacts:
                if (
                    getattr(artifact, "status", "") != "succeeded"
                    or getattr(artifact, "type", "") != "chart"
                ):
                    continue
                output = getattr(artifact, "output", None)
                uri = getattr(output, "uri", None) if output is not None else None
                filename = os.path.basename(str(uri or "").split("?", 1)[0])
                if filename and filename in fixed_html:
                    required_chart_artifact_ids.append(artifact.artifact_id)
            explicit_non_image_binding = _has_non_image_evidence_binding(fixed_html)
            fixed_html = _bind_generated_chart_images(fixed_html, artifacts)
            if successful_artifact_ids and not explicit_non_image_binding:
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    "TRACEABILITY_BINDING_REQUIRED: Generated chart "
                                    "images were linked automatically, but this report "
                                    "still has no explicit KPI/table/claim binding. Add "
                                    "a real SQL/Reference artifact ID to each visible "
                                    "data card and wrap every conclusion number in "
                                    "<strong data-dbgpt-artifacts=\"...\">.</strong>"
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            binding_error = _validate_evidence_bindings(
                fixed_html,
                successful_artifact_ids,
                required_chart_artifact_ids,
                # A run may create exploratory native charts. Only a chart
                # explicitly bound to a report figure is selected for display.
                None,
                {
                    artifact.artifact_id: getattr(artifact, "type", "")
                    for artifact in artifacts
                    if getattr(artifact, "status", "") == "succeeded"
                },
            )
            if binding_error:
                react_state["_report_html_error"] = binding_error
                return json.dumps(
                    {"chunks": [{"output_type": "text", "content": binding_error}]},
                    ensure_ascii=False,
                )
            fixed_html, presentation_bindings = _prepare_presentation_bindings(
                fixed_html
            )
            react_state["_report_presentation_bindings"] = presentation_bindings
            react_state["_report_presentation"] = {
                "format": "html",
                "title": title,
                "content": fixed_html,
                "sha256": hashlib.sha256(fixed_html.encode("utf-8")).hexdigest(),
                "binding_count": len(presentation_bindings),
            }
            react_state.pop("_report_html_error", None)
        except Exception:
            logger.exception("Unable to prepare structured report evidence bindings")
            react_state["_report_html_error"] = (
                "TRACEABILITY_BINDING_ERROR: The report evidence mapping could not "
                "be prepared."
            )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": (
                                "TRACEABILITY_BINDING_ERROR: The report evidence "
                                "mapping could not be prepared. Keep the completed "
                                "analysis results and report this system error; do not "
                                "rerun successful SQL or Python steps."
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            )

        chunks: List[Dict[str, Any]] = [
            {
                "output_type": "text",
                "content": (
                    "✅ HTML 报告已成功渲染并展示给用户。报告任务已完成，"
                    "请勿重复调用 html_interpreter 生成报告。"
                    "若全部目标已达成，请直接调用 terminate 结束。"
                ),
            },
            {"output_type": "html", "content": fixed_html, "title": title},
        ]
        return json.dumps({"chunks": chunks}, ensure_ascii=False)

    return html_interpreter
