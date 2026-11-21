"""Send judge requests to a model on Amazon Bedrock, through its Converse API.

bedrock_transport returns a transport for ChatEndpointJudge. The judge still builds the
prompt, checks the nonce, reads the reply and saves grades. Only the request changes.
boto3 is needed only when no client is passed in. Install it with the bedrock extra.
"""

import importlib

from redline import judge

# Bedrock error codes that mean try again later. Anything else fails at once.
RETRY_CODES = (
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
)

__all__ = ["RETRY_CODES", "BedrockError", "bedrock_transport"]


class BedrockError(ConnectionError):
    """The Bedrock call failed. It is an OSError, so the judge marks the grade unreachable."""


def _code(exc: Exception) -> str:
    """The Bedrock error code of a botocore error, or the class name of any other."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return type(exc).__name__


def _client(region, profile):
    try:
        boto3 = importlib.import_module("boto3")
    except ImportError:
        raise ImportError(
            "the Bedrock judge needs boto3, which is not installed. From a checkout, install "
            'it with: pip install -e ".[bedrock]"'
        ) from None
    return boto3.Session(profile_name=profile, region_name=region).client("bedrock-runtime")


def bedrock_transport(
    model_id: str,
    client=None,
    region: str | None = None,
    profile: str | None = None,
    tries: int = judge.TRIES,
    backoff: tuple = judge.BACKOFF_SECONDS,
    temperature: bool = True,
    max_tokens: int | None = None,
):
    """Return a callable that sends a judge request to Bedrock and returns the reply text.

    model_id is a Bedrock model or inference profile id. client is a bedrock-runtime
    client. None makes one from region and profile with boto3. A throttled or busy
    model is tried again, up to tries times, waiting backoff seconds between tries.
    Inside a judge.deadline block it sends nothing once the time is up and does not
    wait to try again past it. A request already sent runs to the client's own timeout.
    Every failure raises BedrockError, which ChatEndpointJudge turns into review.
    temperature False leaves the judge's temperature out, for a model that refuses it.
    max_tokens replaces the judge's limit of 600, for a model that reasons before it
    answers and counts that against the limit.
    """
    if tries < 1:
        raise ValueError(f"tries must be at least 1, got {tries}")
    client = client if client is not None else _client(region, profile)

    def send(payload: dict) -> str:
        messages = [
            {"role": m["role"], "content": [{"text": m["content"]}]} for m in payload["messages"]
        ]
        config = {"maxTokens": max_tokens or payload.get("max_tokens", 600)}
        if temperature and "temperature" in payload:
            config["temperature"] = payload["temperature"]
        for attempt in range(tries):
            left = judge._time_left()
            if left is not None and left <= 0:
                raise TimeoutError("the judge deadline passed before the request was sent")
            try:
                reply = client.converse(modelId=model_id, messages=messages, inferenceConfig=config)
                blocks = reply["output"]["message"]["content"]
                return "".join(block.get("text", "") for block in blocks)
            except (KeyError, TypeError, IndexError) as exc:
                raise BedrockError(f"the Bedrock reply had no text: {exc!r}") from exc
            except Exception as exc:
                # botocore errors share no class that can be named without importing it.
                code = _code(exc)
                if code not in RETRY_CODES:
                    raise BedrockError(f"{code}: {exc}") from exc
                error = BedrockError(f"{code}: {exc}")
            wait = backoff[min(attempt, len(backoff) - 1)]
            left = judge._time_left()
            if attempt == tries - 1 or (left is not None and wait >= left):
                raise error
            judge._sleep(wait)

    return send
