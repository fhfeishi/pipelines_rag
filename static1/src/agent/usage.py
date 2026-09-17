"""Request-local provider usage; count each model run once, never estimate missing data."""

from langchain_core.callbacks import AsyncCallbackHandler


class TurnUsage(AsyncCallbackHandler):
    def __init__(self):
        self.runs = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.runs.setdefault(run_id, None)

    async def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.runs.setdefault(run_id, None)

    async def on_llm_end(self, response, *, run_id, **kwargs):
        usage = None
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if usage:
                    break
            if usage:
                break
        if not usage:
            raw = (response.llm_output or {}).get("token_usage") or {}
            usage = {"input_tokens": raw.get("prompt_tokens"), "output_tokens": raw.get("completion_tokens"),
                     "total_tokens": raw.get("total_tokens")}
        values = [usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
        valid = all(type(value) is int and value >= 0 for value in values)
        self.runs[run_id] = values if valid else None

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.runs.setdefault(run_id, None)

    def snapshot(self):
        known = [value for value in self.runs.values() if value is not None]
        complete = len(known) == len(self.runs)
        totals = [sum(value[i] for value in known) for i in range(3)]
        return {"input_tokens": totals[0] if complete else None,
                "output_tokens": totals[1] if complete else None,
                "total_tokens": totals[2] if complete else None,
                "reported_tokens": totals[2] if known else None,
                "calls": len(self.runs), "reported_calls": len(known), "complete": complete}
