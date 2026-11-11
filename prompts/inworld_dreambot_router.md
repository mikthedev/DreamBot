# Inworld Router — `dreambot`

Paste the message templates JSON into the Inworld Portal for router **`inworld/dreambot`**.

## Where

1. Open [Inworld Portal](https://portal.inworld.ai) → your **dreambot** router  
2. Default route → variant → **Message templates**  
3. Paste [`inworld_dreambot_message_templates.json`](./inworld_dreambot_message_templates.json)  
4. Save / publish the router  

## Message templates (Portal)

Use this exact shape (system template + compression + prompt variables):

```json
[
  {
    "role": "system",
    "content": "…Dream rules with {{ character_name }}, {{ reply_language }}, {{ mode }}…",
    "compression": {
      "aggressiveness": 0.7
    }
  }
]
```

Full ready-to-paste file: [`inworld_dreambot_message_templates.json`](./inworld_dreambot_message_templates.json)

Plain system text only (if the Portal UI asks for content, not JSON): [`inworld_dreambot_system.txt`](./inworld_dreambot_system.txt)

## Prompt variables the bot sends

| Variable | Example | Purpose |
|---|---|---|
| `character_name` | `Dream` | Identity |
| `reply_language` | `English` / `Russian` / `Ukrainian` | Language lock |
| `mode` | `voice` / `text` | Reply length / spoken style |

The Discord bot still also injects `REPLY LANGUAGE`, `WEB FACTS`, `PATCH FACTS`, and style hints in the user turn for live data.

## Suggested variant settings

| Field | Value |
|---|---|
| `variant_id` | `dream-default` |
| `model_id` | your preferred chat model (or `auto`) |
| `temperature` | `0.35` |
| `max_tokens` | `256` |

## App env

```bash
INWORLD_API_KEY=…          # Portal Base64 key, as-is
INWORLD_MODEL=inworld/dreambot
# After the Portal template is live:
INWORLD_USE_LOCAL_SYSTEM=0
```

While `INWORLD_USE_LOCAL_SYSTEM=1` (default), the bot still sends a resolved local system message so replies work before the Portal template is saved.
