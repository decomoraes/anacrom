"""Jev, TypeSafe's System One model, over its HTTP API.

One request evaluates one ``state`` against a map of named questions and comes
back with one typed answer per question: a Noul (the probability of yes), a
Choice (the winning option and the whole distribution) or a Score (a
probability-weighted level).  Choice and Score answers carry a ``confidence``
the caller gates on, which is what lets a bot act when the model is sure and
hold back when it is not.

Standard library only, like the rest of the client -- the official SDK would
be the project's one dependency.

    https://docs.typesafe.ai/api
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

# Worth another try after a pause; everything else is our mistake or theirs.
RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


class JevError(Exception):
    def __init__(self, message: str, status: int = 0,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


# --------------------------------------------------------------------------
# questions
# --------------------------------------------------------------------------

def noul(instructions, true=None, false=None) -> dict:
    """A yes/no question. The answer is the probability of yes."""
    question = {"type": "noul", "instructions": instructions}
    if true is not None or false is not None:
        question["criteria"] = {k: v for k, v in (("true", true), ("false", false))
                                if v is not None}
    return question


def choice(instructions, options: dict) -> dict:
    """Pick one of up to 255 options, each mapped to what it means (or None)."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions, levels: list) -> dict:
    """Rate along 2 to 10 ordered levels. The answer can land between levels."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

@dataclass
class Response:
    model: str
    answers: dict[str, dict]
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("input_tokens", 0))


class Jev:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, url: str = API_URL,
                 timeout: float = 10.0, retries: int = 4) -> None:
        if not api_key:
            raise JevError("no TypeSafe API key -- export TYPESAFE_API_KEY or run: "
                           "uo set typesafe_api_key <key>", status=401)
        self.api_key = api_key
        self.model = model
        self.url = url
        self.timeout = timeout
        self.retries = retries

    def ask(self, state, questions: dict[str, dict]) -> Response:
        """Evaluate every question against ``state`` in one request."""
        body = json.dumps({"state": state, "model": self.model,
                           "questions": questions}).encode("utf-8")
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                payload = self._post(body)
                break
            except JevError as exc:
                attempt += 1
                if exc.status not in RETRYABLE or attempt > self.retries:
                    raise
                if exc.retry_after is not None:
                    time.sleep(min(exc.retry_after, 10.0))
                else:
                    time.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)) * (0.5 + random.random()))

        return Response(
            model=payload.get("model", self.model),
            answers=payload.get("answers", {}),
            usage=payload.get("usage", {}),
            seconds=round(time.monotonic() - started, 3),
        )

    def _post(self, body: bytes) -> dict:
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                raw = reply.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            try:
                retry_after = float(exc.headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            raise JevError(f"TypeSafe answered {exc.code}: {detail}",
                           status=exc.code, retry_after=retry_after) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            # status 503 so a dropped connection is retried like an overload.
            raise JevError(f"could not reach TypeSafe: {exc}", status=503) from None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise JevError(f"TypeSafe sent something that is not JSON: {raw[:200]!r}") from None
