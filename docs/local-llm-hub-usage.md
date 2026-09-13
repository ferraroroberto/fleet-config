# Using the local LLM hub from downstream code

Examples and limitations behind `global-CLAUDE.md`'s "`local-llm-hub` local LLM hub" entry, which keeps the endpoints and routing rule. Live model ids and ports: the `local-llm-hub` README + `docs/model-comparison.md`, or `GET /v1/models`.

```python
from anthropic import Anthropic
client = Anthropic(api_key="local-dummy", base_url="http://127.0.0.1:8000")
client.messages.create(model="claude-haiku-4-5", ...)
```

```bash
curl -F file=@clip.wav http://127.0.0.1:8090/v1/audio/transcriptions
```

**Limitations:** image/document content blocks work on the subscription paths, but only via the Anthropic `/v1/messages` shape (base64-decoded to a per-request temp dir, fed via `--add-dir`); llama-server backends are text-only and 400 on image input. Also: OpenAI-shape → claude silently drops `image_url` parts; URL image sources are passed as a text reference, not fetched; extended-thinking blocks are dropped at the shape boundary; no streaming on `/v1/messages`; Anthropic-shape tool-use to the open-weight backends is unimplemented (OpenAI-shape works via `--jinja`).
