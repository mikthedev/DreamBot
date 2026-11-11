# Inworld Router — `dreambot`

Paste this into the Inworld Portal for router **`inworld/dreambot`**.

## Where

1. Open [Inworld Portal](https://portal.inworld.ai) → your **dreambot** router  
2. Default route → variant → **Message templates**  
3. Add a **system** message  
4. Paste the full contents of [`inworld_dreambot_system.txt`](./inworld_dreambot_system.txt)  
5. Save / publish the router  

## Suggested variant settings

| Field | Value |
|---|---|
| `variant_id` | `dream-default` |
| `model_id` | your preferred chat model (or `auto`) |
| `temperature` | `0.35` |
| `max_tokens` | `256` (voice stays shorter from the bot side) |

## Message template (Portal)

Role: `system`  
Content: *(entire file `inworld_dreambot_system.txt`)*

Optional variables you can add later in the template:

```text
Mode hint: {{ mode | default("text") }}
Preferred reply language: {{ reply_language | default("match the user") }}
```

The Discord bot still injects `REPLY LANGUAGE`, `WEB FACTS`, `PATCH FACTS`, and voice/text style on each turn — those stay in the user message so live data and language lock keep working.

## App env

```bash
INWORLD_API_KEY=…          # Portal Base64 key, as-is
INWORLD_MODEL=inworld/dreambot
```

The bot also loads the same system text from `prompts/inworld_dreambot_system.txt` so local replies match the router even before Portal templates are saved. Once the Portal template is live, you can set `INWORLD_USE_LOCAL_SYSTEM=0` to avoid sending the system prompt twice.
