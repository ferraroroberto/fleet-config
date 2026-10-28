# Using the local LLM hub from downstream code

Audio endpoints, examples and limitations behind `global-CLAUDE.md`'s "`local-llm-hub` local LLM hub" entry, which keeps the endpoints and routing rule. Live model ids and ports: the `local-llm-hub` README + `docs/model-comparison.md`, or `GET /v1/models`.

**Audio:** POST to the hub on `127.0.0.1:8000` — `/v1/audio/transcriptions` and `/v1/audio/translations` (OpenAI-compatible). The hub places whisper on a host chain of fleet machines (its `config/models.yaml`) and proxies to whichever is up, so requests also land in the observability ring. A whisper-server port such as `:8090` is a host-local detail of whichever machine owns the model and may not listen on this PC at all; never use it as the default.

```python
from anthropic import Anthropic
client = Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")
client.messages.create(model="claude-haiku-4-5", ...)
```

```bash
curl -F file=@clip.wav http://127.0.0.1:8000/v1/audio/transcriptions
```

**Limitations:** image/document content blocks work on the subscription paths, but only via the Anthropic `/v1/messages` shape (base64-decoded to a per-request temp dir, fed via `--add-dir`); llama-server backends are text-only and 400 on image input. Also: OpenAI-shape → claude silently drops `image_url` parts; URL image sources are passed as a text reference, not fetched; extended-thinking blocks are dropped at the shape boundary; no streaming on `/v1/messages`; Anthropic-shape tool-use to the open-weight backends is unimplemented (OpenAI-shape works via `--jinja`).
