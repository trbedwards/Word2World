import json
import os
import shutil
import subprocess
import tempfile


DEFAULT_TIMEOUT_SECONDS = 300


def _timeout_seconds():
    value = os.environ.get("WORD2WORLD_CLI_TIMEOUT_SECONDS")
    if not value:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return int(value)
    except ValueError:
        raise ValueError("WORD2WORLD_CLI_TIMEOUT_SECONDS must be an integer")


def _split_selector(model):
    if not model:
        return "codex", None

    model = str(model)
    lowered = model.lower()

    if lowered == "codex":
        return "codex", None
    if lowered.startswith("codex:"):
        return "codex", model.split(":", 1)[1] or None

    if lowered in {"claude", "anthropic"}:
        return "claude", None
    if lowered.startswith("claude:") or lowered.startswith("anthropic:"):
        return "claude", model.split(":", 1)[1] or None
    if lowered.startswith("claude-") or lowered in {"sonnet", "opus", "haiku", "fable"}:
        return "claude", model

    return "codex", model


def _role_label(role):
    if role == "assistant":
        return "Assistant"
    if role == "system":
        return "System"
    return "User"


def _messages_to_prompt(messages):
    parts = [
        "You are the chat-completion backend for Word2World.",
        "Answer the next assistant turn only. Do not inspect files, edit files, or use tools.",
        "",
        "Conversation:",
    ]
    for message in messages:
        role = _role_label(message.get("role", "user"))
        content = message.get("content", "")
        parts.append(f"{role}:\n{content}")
        parts.append("")
    parts.append("Assistant:")
    return "\n".join(parts)


def _usage(prompt_tokens=0, completion_tokens=0, total_cost_usd=None):
    usage = {
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    if total_cost_usd is not None:
        usage["total_cost_usd"] = total_cost_usd
    return usage


def _chat_response(content, model, usage):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ],
        "model": model,
        "usage": usage,
    }


class CLIChatCompletion:
    def create(self, model, messages, temperature=None):
        provider, cli_model = _split_selector(model)
        prompt = _messages_to_prompt(messages)

        if provider == "claude":
            content, usage = self._run_claude(prompt, cli_model)
        else:
            content, usage = self._run_codex(prompt, cli_model)

        return _chat_response(content, model, usage)

    def _run_claude(self, prompt, model):
        command = os.environ.get("WORD2WORLD_CLAUDE_COMMAND", "claude")
        if not shutil.which(command):
            raise RuntimeError(f"Claude Code CLI command not found: {command}")

        args = [
            command,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--tools",
            "",
        ]
        if model:
            args.extend(["--model", model])

        completed = subprocess.run(
            args,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=_timeout_seconds(),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(_format_cli_error("Claude Code", completed))

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude Code returned invalid JSON: {exc}") from exc

        if payload.get("is_error"):
            raise RuntimeError(payload.get("result") or "Claude Code returned an error")

        usage_payload = payload.get("usage") or {}
        return (
            payload.get("result", "").strip(),
            _usage(
                prompt_tokens=usage_payload.get("input_tokens", 0),
                completion_tokens=usage_payload.get("output_tokens", 0),
                total_cost_usd=payload.get("total_cost_usd"),
            ),
        )

    def _run_codex(self, prompt, model):
        command = os.environ.get("WORD2WORLD_CODEX_COMMAND", "codex")
        if not shutil.which(command):
            raise RuntimeError(f"Codex CLI command not found: {command}")

        output_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="word2world-codex-", suffix=".txt", delete=False) as output_file:
                output_path = output_file.name

            args = [
                command,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--output-last-message",
                output_path,
            ]
            if model:
                args.extend(["--model", model])
            args.append("-")

            completed = subprocess.run(
                args,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=_timeout_seconds(),
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(_format_cli_error("Codex", completed))

            with open(output_path, "r", encoding="utf-8") as output_file:
                content = output_file.read().strip()

            return content, _usage()
        finally:
            if output_path and os.path.exists(output_path):
                os.unlink(output_path)


def _format_cli_error(name, completed):
    output = (completed.stderr or completed.stdout or "").strip()
    if output:
        return f"{name} CLI failed with exit code {completed.returncode}:\n{output}"
    return f"{name} CLI failed with exit code {completed.returncode}"


class CLIChatModule:
    ChatCompletion = CLIChatCompletion()


cli_chat = CLIChatModule()
