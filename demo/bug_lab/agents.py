"""Triage, Diagnosis, Remediation and Comms agents for the bug lab.

Agents see only what the monitor and verifier observe plus the store files.
They never receive the injected bug's id. A rule engine always works offline;
when a model backend is configured, Diagnosis and Remediation ask the model
first and fall back to the rules if its answer is unusable.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from demo.bug_lab.checks import Check
from demo.bug_lab.llm import Backend
from demo.bug_lab.workspace import EDITABLE_FILES, MONITOR_TAG, Workspace, unified_diff

MAX_CHANGED_LINES = 40


@dataclass
class Step:
    kind: str  # tool, thought, result or warning
    text: str
    detail: str = ""


@dataclass
class Diagnosis:
    file: str
    root_cause: str
    author: str
    steps: list[Step] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {"file": self.file, "root_cause": self.root_cause, "author": self.author}


@dataclass
class Patch:
    file: str
    edits: list[dict[str, str]]
    explanation: str
    author: str
    kind: str = "patch"  # patch or rollback

    def digest(self, incident_id: str) -> str:
        canonical = json.dumps({"incident": incident_id, **asdict(self)}, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PatchError(ValueError):
    pass


# --- Triage -----------------------------------------------------------------

_TRIAGE = (
    # check name, severity, incident title
    ("page_loads", "P1", "Store page is down"),
    ("products_api", "P1", "Product API is failing"),
    ("scripts_load", "P1", "Store script failed to load"),
    ("js_errors", "P1", "JavaScript errors on the store page"),
    ("products_rendered", "P1", "Products are not rendering"),
    ("prices_numeric", "P1", "Prices are wrong"),
    ("prices_display", "P1", "Prices are wrong"),
    ("add_to_cart", "P1", "Add to cart is broken"),
    ("monitor_present", "P2", "Monitor script was removed"),
    ("stylesheets_load", "P2", "Store styling is missing"),
    ("styles_applied", "P2", "Store styling is missing"),
    ("api_fast", "P2", "Product API is slow"),
    ("api_fast_browser", "P2", "Product API is slow"),
    ("images_load", "P3", "Product images are broken"),
    ("images_display", "P3", "Product images are broken"),
    ("fits_screen", "P3", "Layout overflows the screen"),
    ("requests_ok", "P3", "Requests are failing"),
)


def triage(failing: list[Check]) -> tuple[str, str, list[Step]]:
    names = {check.name for check in failing}
    matches = [(severity, title, name) for name, severity, title in _TRIAGE if name in names]
    if not matches:
        return "P3", "Store check failing", [Step("thought", "No known signal pattern; defaulting to P3.")]
    severity = min(severity for severity, _, _ in matches)
    title = next(title for item_severity, title, _ in matches if item_severity == severity)
    reasons = ", ".join(sorted({name for item_severity, _, name in matches if item_severity == severity}))
    impact = {"P1": "customers cannot browse or buy", "P2": "the store works but is degraded",
              "P3": "cosmetic or partial impact"}[severity]
    return severity, title, [Step("thought", f"Signals {reasons} → {severity}: {impact}.")]


# --- Patch mechanics ------------------------------------------------------

def _find_tolerant(text: str, find: str) -> tuple[int, int] | None:
    """Locate ``find`` ignoring trailing whitespace on each line; None unless unique."""
    lines = text.splitlines(keepends=True)
    wanted = [line.rstrip() for line in find.strip("\n").splitlines()]
    if not wanted:
        return None
    hits = [
        index for index in range(len(lines) - len(wanted) + 1)
        if [line.rstrip() for line in lines[index:index + len(wanted)]] == wanted
    ]
    if len(hits) != 1:
        return None
    start = sum(len(line) for line in lines[:hits[0]])
    block = "".join(lines[hits[0]:hits[0] + len(wanted)])
    end = start + len(block.rstrip("\r\n"))
    return start, end


def apply_edits(text: str, edits: list[dict[str, str]]) -> str:
    if not edits:
        raise PatchError("The patch has no edits")
    for edit in edits:
        find, replace = edit.get("find", ""), edit.get("replace", "")
        if not find.strip():
            raise PatchError("An edit has an empty FIND block")
        count = text.count(find)
        if count == 1:
            text = text.replace(find, replace, 1)
            continue
        if count > 1:
            raise PatchError(f"FIND text matches {count} places; it must match exactly one")
        span = _find_tolerant(text, find)
        if span is None:
            raise PatchError("FIND text was not found in the file")
        text = text[:span[0]] + replace.strip("\n") + text[span[1]:]
    return text


def edit_between(old: str, new: str) -> list[dict[str, str]]:
    """One line-aligned edit turning ``old`` into ``new``, with unique context."""
    old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
    start = 0
    while start < min(len(old_lines), len(new_lines)) and old_lines[start] == new_lines[start]:
        start += 1
    tail = 0
    while (tail < min(len(old_lines), len(new_lines)) - start
           and old_lines[-1 - tail] == new_lines[-1 - tail]):
        tail += 1

    def segment(lines: list[str]) -> str:
        return "".join(lines[start:len(lines) - tail])

    while (not segment(old_lines).strip() or old.count(segment(old_lines)) != 1) and (start or tail):
        if start:
            start -= 1
        else:
            tail -= 1
    return [{"find": segment(old_lines), "replace": segment(new_lines)}]


def safety_check(workspace: Workspace, patch: Patch) -> tuple[str, list[str]]:
    """Dry-run a patch on a copy of the file; refuse anything unsafe or ineffective."""
    if patch.file not in EDITABLE_FILES:
        raise PatchError(f"{patch.file!r} is not an editable store file")
    before = workspace.read(patch.file)
    after = apply_edits(before, patch.edits)
    if after == before:
        raise PatchError("The patch changes nothing")
    if patch.file.endswith(".json"):
        try:
            json.loads(after)
        except ValueError as exc:
            raise PatchError(f"The patched file is not valid JSON: {exc}") from exc
    if patch.file == "index.html" and MONITOR_TAG not in after:
        raise PatchError("The patch would remove the monitor script")
    diff = unified_diff(patch.file, before, after)
    changed = sum(1 for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---")))
    if changed > MAX_CHANGED_LINES:
        raise PatchError(f"The patch changes {changed} lines; the limit is {MAX_CHANGED_LINES}")
    return after, diff


# --- Rule-based diagnosis ---------------------------------------------------

RULES_AUTHOR = "Rule engine (no model)"
_JS_GLOBALS = {
    "fetch", "Number", "String", "Boolean", "Array", "Object", "JSON", "Math", "Error",
    "Promise", "setTimeout", "clearTimeout", "parseFloat", "parseInt", "console",
    "document", "window", "requestAnimationFrame", "encodeURIComponent", "alert",
}
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof", "new",
                "await", "async"}
_REFERENCE_PATTERNS = (
    re.compile(r"([A-Za-z_$][\w$]*) is not defined"),
    re.compile(r"Can't find variable: ([A-Za-z_$][\w$]*)"),
)


def _js_definitions(source: str) -> set[str]:
    names = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", source))
    names |= set(re.findall(r"\b(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)", source))
    return names


def _undefined_calls(source: str) -> list[str]:
    defined = _js_definitions(source) | _JS_GLOBALS | _JS_KEYWORDS
    calls = re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", source)
    return list(dict.fromkeys(name for name in calls if name not in defined))


def _closest(name: str, options: list[str] | set[str]) -> str | None:
    found = difflib.get_close_matches(name, sorted(options), n=1, cutoff=0.6)
    return found[0] if found else None


def _line_of(text: str, needle: str) -> int:
    index = text.find(needle)
    return text.count("\n", 0, index) + 1 if index >= 0 else 0


def _repair_json(text: str) -> str | None:
    body = re.sub(r",\s*$", "", text.rstrip())
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    stack: list[str] = []
    in_string = escaped = False
    for char in body:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return None
            stack.pop()
    for depth, opener in reversed(list(enumerate(stack))):
        body += "\n" + "  " * depth + ("}" if opener == "{" else "]")
    body += "\n"
    try:
        json.loads(body)
    except ValueError:
        return None
    return body


def diagnose_rules(failing: list[Check], workspace: Workspace) -> Diagnosis | None:
    by_name = {check.name: check for check in failing}

    def found(file: str, cause: str, fix: str, steps: list[Step], **data: Any) -> Diagnosis:
        return Diagnosis(file, cause, RULES_AUTHOR, steps, {"fix": fix, **data})

    if "products_api" in by_name:
        steps = [Step("tool", "http_get /shop/api/products", by_name["products_api"].detail),
                 Step("tool", "read_file products.json")]
        try:
            json.loads(workspace.read("products.json"))
        except ValueError as exc:
            return found("products.json", f"products.json is not valid JSON: {exc}", "repair_json", steps)

    if "stylesheets_load" in by_name or "scripts_load" in by_name:
        check = by_name.get("stylesheets_load") or by_name["scripts_load"]
        suffix, attribute = (".css", "href") if check.name == "stylesheets_load" else (".js", "src")
        for failure in check.evidence.get("failures", []):
            candidates = [name for name in workspace.files() if name.endswith(suffix)]
            best = _closest(failure["url"], candidates)
            steps = [Step("tool", f"http_get /shop/{failure['url']}", f"HTTP {failure['status']}"),
                     Step("tool", f"list_files *{suffix}", ", ".join(candidates) or "none")]
            if best:
                return found("index.html",
                             f"index.html references {failure['url']}, which does not exist; "
                             f"the store ships {best}.",
                             "replace_text", steps, old=f'{attribute}="{failure["url"]}"',
                             new=f'{attribute}="{best}"')

    if "prices_numeric" in by_name:
        bad = by_name["prices_numeric"].evidence.get("bad_prices", [])
        steps = [Step("tool", "http_get /shop/api/products",
                      f"{len(bad)} products have non-numeric prices"),
                 Step("tool", "read_file products.json")]
        sample = ", ".join(json.dumps(item["price"]) for item in bad[:2])
        return found("products.json",
                     f"products.json stores prices as text ({sample}), so the page computes NaN.",
                     "numeric_prices", steps)

    if "images_load" in by_name:
        missing = by_name["images_load"].evidence.get("missing", [])
        files = workspace.files()
        mapping = {}
        for item in missing:
            image = item.get("image")
            if not isinstance(image, str):
                continue
            base = image.rsplit("/", 1)[-1]
            hits = [name for name in files if name.rsplit("/", 1)[-1] == base]
            if len(hits) == 1:
                mapping[image] = hits[0]
        steps = [Step("tool", f"http_get {len(missing)} product images", "all returned 404"),
                 Step("tool", "list_files images/", f"found {len(mapping)} of them elsewhere")]
        if mapping:
            example_old, example_new = next(iter(mapping.items()))
            return found("products.json",
                         f"Product image paths point at a missing folder ({example_old}); "
                         f"the files exist at {example_new}.",
                         "image_paths", steps, mapping=mapping)

    if "api_fast" in by_name or "api_fast_browser" in by_name:
        check = by_name.get("api_fast") or by_name["api_fast_browser"]
        steps = [Step("tool", "time http_get /shop/api/products", check.detail),
                 Step("tool", "read_file config.json")]
        try:
            config = json.loads(workspace.read("config.json"))
        except ValueError:
            config = {}
        slow = {key: value for key, value in config.items()
                if any(word in key for word in ("delay", "latency", "sleep"))
                and isinstance(value, (int, float)) and value > 0}
        if slow:
            key, value = next(iter(slow.items()))
            return found("config.json",
                         f"config.json sets {key} to {value}, adding that delay to every product request.",
                         "zero_delay", steps, key=key)

    browser_errors = by_name.get("js_errors")
    source = workspace.read("app.js")
    if browser_errors:
        names = [match.group(1) for message in browser_errors.evidence.get("messages", [])
                 for pattern in _REFERENCE_PATTERNS for match in [pattern.search(message)] if match]
        steps = [Step("tool", "browser console", browser_errors.detail),
                 Step("tool", "read_file app.js")]
        names = names or _undefined_calls(source)
        for name in names:
            best = _closest(name, _js_definitions(source))
            if best and best != name:
                return found("app.js",
                             f"app.js line {_line_of(source, name + '(')} calls {name}(), which is "
                             f"not defined; the function is named {best}().",
                             "rename_call", steps, old=name, new=best)

    if "add_to_cart" in by_name or browser_errors:
        html = workspace.read("index.html")
        known = set(re.findall(r'\bid="([^"]+)"', html)) | set(re.findall(r'\.id\s*=\s*"([^"]+)"', source))
        wanted = re.findall(r'getElementById\("([^"]+)"\)', source)
        missing = [element for element in dict.fromkeys(wanted) if element not in known]
        steps = [Step("tool", "browser synthetic check: click Add to cart",
                      (by_name.get("add_to_cart") or browser_errors).detail),
                 Step("tool", "read_file app.js"), Step("tool", "read_file index.html",
                                                        f"element ids: {', '.join(sorted(known))}")]
        for element in missing:
            best = _closest(element, known)
            if best:
                return found("app.js",
                             f'app.js line {_line_of(source, element)} looks up id "{element}", '
                             f'which is not on the page; the element is "{best}".',
                             "replace_text", steps, old=f'getElementById("{element}")',
                             new=f'getElementById("{best}")')

    if "fits_screen" in by_name:
        css = workspace.read("styles.css")
        steps = [Step("tool", "browser layout check", by_name["fits_screen"].detail),
                 Step("tool", "read_file styles.css")]
        for match in re.finditer(r"(?m)^(\s*)(?:min-)?width:\s*(\d+)px;", css):
            if int(match.group(2)) >= 1200:
                selector = css[:match.start()].rsplit("}", 1)[-1].split("{")[0].strip()
                return found("styles.css",
                             f"styles.css gives {selector} a fixed width of {match.group(2)}px, "
                             "wider than any screen.",
                             "replace_text", steps, old=match.group(0),
                             new=f"{match.group(1)}width: 100%;")
    return None


def remediate_rules(diagnosis: Diagnosis, workspace: Workspace) -> Patch | None:
    before = workspace.read(diagnosis.file)
    fix, data = diagnosis.data.get("fix"), diagnosis.data
    after = None
    if fix == "replace_text" and before.count(data["old"]) == 1:
        after = before.replace(data["old"], data["new"])
        explanation = f"Replace {data['old']} with {data['new']}."
    elif fix == "repair_json":
        after = _repair_json(before)
        explanation = "Remove the dangling comma and restore the missing closing brackets."
    elif fix == "numeric_prices":
        def to_number(match: re.Match[str]) -> str:
            number = re.search(r"[-+]?\d+(?:\.\d+)?", match.group(1))
            return f'"price": {json.dumps(float(number.group(0)))}' if number else match.group(0)

        after = re.sub(r'"price": "([^"]*)"', to_number, before)
        explanation = "Store each price as a number again."
    elif fix == "image_paths":
        after = before
        for old, new in data["mapping"].items():
            after = after.replace(f'"image": "{old}"', f'"image": "{new}"')
        explanation = "Point each product image at the file that exists."
    elif fix == "zero_delay":
        after = re.sub(rf'("{re.escape(data["key"])}":\s*)\d+(?:\.\d+)?', r"\g<1>0", before)
        explanation = f"Set {data['key']} back to 0."
    elif fix == "rename_call":
        after = re.sub(rf"(?<![\w$.]){re.escape(data['old'])}(?=\s*\()", data["new"], before)
        explanation = f"Call {data['new']}() instead of the undefined {data['old']}()."
    if not after or after == before:
        return None
    return Patch(diagnosis.file, edit_between(before, after), explanation, RULES_AUTHOR)


# --- Model-backed diagnosis and remediation ------------------------------

DIAGNOSIS_SYSTEM = """You are the Diagnosis agent in AtlasOps, an incident-response system for a small web store.
Find the single store file that causes the failing checks and state the root cause.
Reply in exactly this format and nothing else:
FILE: <one of index.html, styles.css, app.js, products.json, config.json>
ROOT_CAUSE: <one sentence naming the exact faulty value or line>"""

REMEDIATION_SYSTEM = """You are the Remediation agent in AtlasOps. Fix the root cause with the smallest possible edit to the named file.
Copy the lines you change exactly as they appear in the file, without the line numbers, then give their replacement.
Reply in exactly this format:
FIND:
<exact existing lines>
REPLACE:
<replacement lines>
END
EXPLANATION: <one sentence>
Repeat FIND/REPLACE/END for separate places if needed. Never remove the line that loads /lab/monitor.js."""


def _numbered(text: str) -> str:
    return "\n".join(f"{number:>3}| {line}" for number, line in enumerate(text.splitlines(), 1))


def _evidence(failing: list[Check]) -> str:
    lines = []
    for check in failing:
        lines.append(f"- [{check.source}] {check.label}: FAIL. {check.detail}".rstrip())
        for message in check.evidence.get("messages", [])[:3]:
            lines.append(f"    browser message: {message}")
    return "\n".join(lines)


def _strip_numbers(block: str) -> str:
    lines = block.splitlines()
    if lines and all(re.match(r"^\s*\d+\|\s?", line) for line in lines):
        return "\n".join(re.sub(r"^\s*\d+\|\s?", "", line) for line in lines)
    return block


def _whole_fenced_answer(answer: str) -> str | None:
    text = answer.replace("\r\n", "\n").strip()
    if "```" not in text:
        return text
    lines = text.splitlines()
    if (len(lines) < 3 or not re.fullmatch(r"```[A-Za-z0-9_-]*", lines[0])
            or lines[-1] != "```"):
        return None
    if any("```" in line for line in lines[1:-1]):
        return None
    return "\n".join(lines[1:-1]).strip()


def _parse_diagnosis_answer(answer: str) -> tuple[str, str] | None:
    text = _whole_fenced_answer(answer)
    if text is None:
        return None
    allowed_files = "|".join(re.escape(name) for name in EDITABLE_FILES)
    match = re.fullmatch(rf"FILE: ({allowed_files})\nROOT_CAUSE: ([^\n]+)", text)
    if not match:
        return None
    cause = match.group(2).strip()
    if not cause:
        return None
    return match.group(1), cause


def _parse_patch_answer(answer: str) -> tuple[list[dict[str, str]], str] | None:
    text = _whole_fenced_answer(answer)
    if text is None:
        return None
    lines = text.split("\n")
    index = 0
    edits = []

    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            return None
        if lines[index].startswith("EXPLANATION: "):
            explanation = lines[index][len("EXPLANATION: "):].strip()
            if not explanation or any(line.strip() for line in lines[index + 1:]):
                return None
            return (edits, explanation) if edits else None
        if lines[index] != "FIND:":
            return None
        index += 1
        find_start = index
        while index < len(lines) and lines[index] != "REPLACE:":
            index += 1
        if index >= len(lines):
            return None
        find_lines = lines[find_start:index]
        find = "\n".join(find_lines)
        if not find.strip():
            return None

        index += 1
        replace_start = index
        while index < len(lines) and lines[index] != "END":
            index += 1
        if index >= len(lines):
            return None
        replacement = "\n".join(lines[replace_start:index])
        edits.append({"find": find, "replace": replacement})
        index += 1

    return None


async def diagnose_llm(backend: Backend, failing: list[Check], workspace: Workspace,
                       feedback: str = "") -> Diagnosis | None:
    files = "\n\n".join(f"=== {name} ===\n{_numbered(workspace.read(name))}" for name in EDITABLE_FILES)
    prompt = f"Failing checks:\n{_evidence(failing)}\n\n"
    if feedback:
        prompt += f"A previous fix attempt failed: {feedback}\n\n"
    prompt += f"Store files (line numbers are not part of the files):\n{files}"
    answer = await backend.chat(DIAGNOSIS_SYSTEM, prompt, max_tokens=200)
    parsed = _parse_diagnosis_answer(answer)
    if not parsed:
        return None
    steps = [Step("tool", f"read_file {name}") for name in EDITABLE_FILES]
    steps.append(Step("thought", f"Model answer: {answer.strip()[:400]}"))
    return Diagnosis(parsed[0], parsed[1], f"{backend.model} (model)", steps)


async def remediate_llm(backend: Backend, diagnosis: Diagnosis, failing: list[Check],
                        workspace: Workspace, feedback: str = "") -> Patch | None:
    prompt = (f"FILE: {diagnosis.file}\nROOT_CAUSE: {diagnosis.root_cause}\n\n"
              f"Failing checks:\n{_evidence(failing)}\n\n")
    if feedback:
        prompt += f"A previous fix attempt failed: {feedback}\n\n"
    prompt += f"Current {diagnosis.file} (line numbers are not part of the file):\n"
    prompt += _numbered(workspace.read(diagnosis.file))
    answer = await backend.chat(REMEDIATION_SYSTEM, prompt, max_tokens=900)
    parsed = _parse_patch_answer(answer)
    if not parsed:
        return None
    edits, explanation = parsed
    text = workspace.read(diagnosis.file)
    for edit in edits:
        find, replace = edit["find"], edit["replace"]
        if find not in text:
            find, replace = _strip_numbers(find), _strip_numbers(replace)
            edit.update(find=find, replace=replace)
    return Patch(diagnosis.file, edits, explanation,
                 f"{backend.model} (model)")


# --- Comms ------------------------------------------------------------------

def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def comms_summary(incident: dict[str, Any]) -> str:
    title = incident["title"] or "Incident closed before triage"
    severity = incident["severity"] or "not triaged"
    parts = [f"{incident['id']} ({severity}): {_sentence(title)}"]
    if incident["status"] == "resolved":
        diagnosis = incident.get("diagnosis") or {}
        patch = incident.get("patch") or {}
        if diagnosis.get("root_cause"):
            parts.append(f"Root cause: {_sentence(diagnosis['root_cause'])}")
        if patch:
            parts.append(f"Fix by {incident.get('resolved_by')}: {_sentence(patch.get('explanation', ''))}")
            parts.append("The verifier confirmed every server and browser check passes.")
        else:
            parts.append("The checks recovered before any change was made.")
    else:
        parts.append(f"Not resolved: {_sentence(incident.get('outcome') or 'no fix was verified')}")
        parts.append("No further change was made.")
    if incident.get("detect_s") is not None:
        timing = f"Detected {incident['detect_s']}s after the change"
        if incident.get("resolve_s") is not None:
            timing += f" and resolved {incident['resolve_s']}s after detection"
        parts.append(_sentence(timing))
    return " ".join(parts)
