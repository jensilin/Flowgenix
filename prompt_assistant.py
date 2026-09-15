"""Flow Prompt Assistant: turn a user's scenario into a ready-to-run flow prompt.

The assistant does not build flows. It writes the *prompt* that the main
"Create flow from prompt" agent will execute, choosing processors from the live
NiFi catalog so the prompt only ever names components this instance actually has.
"""

from __future__ import annotations

import re
from typing import Any

from agent_prompt import apply_deployment_mandate, catalog_block

PROMPT_OPEN = "<flow-prompt>"
PROMPT_CLOSE = "</flow-prompt>"

MAX_HISTORY_TURNS = 8


def _history_block(history: list[dict[str, str]] | None) -> str:
    """Render the recent chat history for the model.

    Includes an explicit refinement instruction: the model should treat the
    latest user turn as a *modification* of the previous flow prompt rather
    than an isolated request. Without this, models frequently rewrite the flow
    from scratch and drop details the user already accepted, making
    conversational refinement feel broken.
    """

    if not history:
        return ""
    lines = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        role = (turn.get("role") or "").strip().lower()
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        who = "User" if role == "user" else "Assistant"
        lines.append(f"{who}: {text}")
    if not lines:
        return ""

    guidance = (
        "This is a MULTI-TURN conversation. The latest user turn is either a "
        "refinement of the previous flow (e.g. \"use SFTP instead\", \"add "
        "retry\", \"log the failures\") or a completely new scenario. If the "
        "user is refining, keep every processor and setting from the previous "
        "flow prompt that they did NOT ask to change; edit only the parts they "
        "did. Do not rewrite the whole flow from scratch when the user only "
        "asked for a small change. If the user is asking a new scenario, "
        "start fresh."
    )
    return (
        "Conversation so far (most recent last):\n"
        + "\n".join(lines)
        + "\n\n"
        + guidance
        + "\n\n"
    )


def build_assistant_prompt(
    scenario: str,
    catalog_summary: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    upload_note: str | None = None,
) -> str:
    version = (catalog_summary or {}).get("version") or "unknown"
    upload_block = f"\nUploaded JSON context (the user has a file loaded):\n{upload_note}\n" if upload_note else ""

    return f"""You are the Flow Prompt Assistant for a NiFi automation studio.

You do NOT build or deploy anything. You are a writing assistant: the user
describes a scenario in their own words, and you reply with the best possible
prompt for the flow-building agent that runs next.

{catalog_block(catalog_summary)}
{upload_block}
{_history_block(history)}User's scenario:
{scenario}

How to answer:
1. Write 1-3 short sentences to the user explaining the pipeline you chose and
   why those processors fit. Plain prose, no headings, no bullet lists, no
   markdown emphasis. If a genuinely important detail is missing, state the
   assumption you made rather than asking a question.
2. Then, if the user described a flow to build, output the prompt itself
   wrapped in {PROMPT_OPEN} and {PROMPT_CLOSE} tags.

Rules for the prompt you write inside the tags:
- Address the flow-building agent directly, as an instruction (e.g. "Create a
  process group named ... that reads ...").
- Name the EXACT processors and controller services from the NiFi {version}
  catalog above. Never invent a type that is not listed. Any installed component
  is available, so choose the one that genuinely fits the scenario (database,
  SFTP, S3, Kafka, MQTT, Elasticsearch, scripting, record processors, ...)
  instead of bending a common processor to the task.
- Give the pipeline in order, the key properties each processor needs, which
  relationships to auto-terminate, and a descriptive process group name.
- Mention controller services (readers/writers) explicitly when the processors
  you chose require them.
- Keep it one focused paragraph or a short ordered list — it must read as a
  single self-contained request, because it is pasted into a prompt box as-is.
- Do not include JSON, code fences, or file paths inside the tags.
- End the prompt with: "Create, configure, connect, validate, and deploy the
  complete flow automatically using the NiFi REST API."

If the user is only asking a question (for example "which processors can I use
for Kafka?"), answer it in prose and omit the {PROMPT_OPEN} tags entirely.

Hard constraints for this task:
- Do NOT create, edit, or delete any file.
- Do NOT run any shell command and do NOT call the NiFi REST API.
- Reading `flows/.nifi-catalog.json` is allowed if you need more detail.
- Your entire answer is shown in a small chat window, so keep it brief.
"""


def extract_flow_prompt(text: str) -> tuple[str, str | None]:
    """Split an assistant reply into (chat reply, flow prompt or None)."""
    raw = (text or "").strip()
    if not raw:
        return "", None

    match = re.search(
        re.escape(PROMPT_OPEN) + r"(.*?)" + re.escape(PROMPT_CLOSE),
        raw,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        # An unclosed opening tag still means everything after it is the prompt.
        loose = re.search(re.escape(PROMPT_OPEN) + r"(.*)", raw, re.DOTALL | re.IGNORECASE)
        if not loose:
            return raw, None
        prompt = loose.group(1)
        reply = raw[: loose.start()]
    else:
        prompt = match.group(1)
        reply = (raw[: match.start()] + " " + raw[match.end() :]).strip()

    prompt = _clean_prompt(prompt)
    if not prompt:
        return raw, None
    return (reply.strip() or "Here is a flow prompt for that scenario."), prompt


def _clean_prompt(prompt: str) -> str:
    text = (prompt or "").strip()
    # Models sometimes wrap the prompt in a code fence despite being told not to.
    fence = re.match(r"^```[a-zA-Z]*\n(.*?)\n?```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text:
        return ""
    return apply_deployment_mandate(text)
