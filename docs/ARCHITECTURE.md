# System architecture

For the detailed walkthrough of every component, see [STUDY.md](STUDY.md). Database and ER diagram: [DATABASE.md](DATABASE.md).

## Components

```mermaid
flowchart TB
    subgraph Callers["Channels"]
        PH["📞 Phone line<br/>Twilio Media Streams<br/>µ-law 8 kHz"]
        BP["📱 Browser phone<br/>(QR) PCM 16 kHz"]
        DB1["💻 Dashboard<br/>text / mic"]
    end

    subgraph Backend["FastAPI backend (Python)"]
        TEL["telephony.py<br/>signed webhook · one-time stream token<br/>allowlisted outbound"]
        VOICE["voice.py<br/>VAD → STT → reply → TTS<br/>fillers · barge-in · echo guard"]
        SVC["service.py · CallService<br/>turn loop · event hub"]
        SES["session.py · CallSession<br/>transcript · intel · stage · read-back gate"]
        RULE["rule_extractor.py<br/>+ validators.py"]
        PER["persona.py<br/>read-back → intent → cache → LLM → script<br/>+ output guard"]
        LLM["llm.py<br/>Gemini model/key pool<br/>(Claude optional)"]
        EXT["extractor.py<br/>JSON-schema extraction"]
        REP["report.py<br/>JSON + PDF"]
        STORE[("SQLite<br/>db.py")]
    end

    subgraph External["External services"]
        GEM["Gemini API"]
        SAR["Sarvam AI<br/>Saaras STT · Bulbul TTS"]
        TW["Twilio"]
    end

    subgraph UI["React frontend"]
        DASH["Dashboard<br/>transcript · stages · intel cards<br/>calls · QR · dispatch"]
        PHONE["Mobile call screen"]
    end

    PH <--> TW <--> TEL --> VOICE
    BP <--> PHONE
    PHONE <--> VOICE
    DB1 --> SVC
    VOICE <--> SAR
    VOICE --> SVC --> SES
    SES --> RULE
    SVC --> PER --> LLM <--> GEM
    SVC --> EXT --> LLM
    SES --> STORE
    SVC -- "WebSocket events" --> DASH
    SES --> REP --> DASH
```

## Data flow of one caller turn

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller (scammer)
    participant V as Voice pipeline
    participant S as CallService / CallSession
    participant P as Persona
    participant G as Gemini pool
    participant D as Dashboard(s)
    participant DB as SQLite

    C->>V: audio frames
    V->>V: VAD detects end of utterance
    V->>V: Sarvam speech-to-text
    V->>S: respond(text)
    S->>S: rule extraction + validators + verbatim-quote check
    S-->>D: turn + state (cards update)
    S->>P: reply?
    alt identifier waiting for confirmation
        P-->>S: read-back generated in code
    else predictable moment ("are you an AI?", "tell me the OTP")
        P-->>S: prepared in-character line
    else seen this situation before
        P-->>S: cached reply
    else
        P->>G: chat (model chain, local rate limit)
        G-->>P: reply → output guard
    end
    Note over V: reply slower than 0.6 s → cached filler plays first
    S-->>V: Paatti's reply → Sarvam text-to-speech → caller hears it
    S-->>D: turn
    S->>DB: persist call, turns, intel, stages, sightings
    S->>G: background JSON extraction (only turns with digits / @ / money)
```

## Deployment

```mermaid
flowchart LR
    J["Judge's phone"] -- "PSTN call" --> TW["Twilio"]
    TW -- "HTTPS webhook + WSS stream" --> T["Public HTTPS<br/>(Render or cloudflared tunnel)"]
    J2["Judge's phone browser"] -- "HTTPS + WSS" --> T
    T --> APP["Docker container<br/>uvicorn: FastAPI + built React"]
    APP --> SQL[("SQLite file")]
    APP -- "HTTPS" --> GEM["Gemini"]
    APP -- "HTTPS" --> SAR["Sarvam"]
    PROJ["Projector: dashboard"] -- "HTTPS + WSS" --> T
```

## Key design decisions
| Decision | Why |
|---|---|
| Read-backs generated in code, not by the LLM | The digits repeated must equal the digits captured; this is what makes the confirmation evidence. |
| Two extractors behind one validation gate | Regex is instant and free; the LLM adds recall; validators + verbatim quotes stop hallucinations either way. |
| Cheapest-first reply ladder | Read-back → intent → cache → LLM → script keeps API usage and latency low and guarantees an answer. |
| Model/key rotation with local rate limiting | Free-tier Gemini limits and overloads (we saw 404 and 503 in testing) must never stall a live call. |
| In-memory session + write-through SQLite | Live calls stay fast; history and the cross-call mule registry survive restarts. |
| One event hub for all screens | The projector can follow a phone call live; any number of watchers. |
| Phone calls are watch-only from dashboards | Nobody with a session ID can inject lines into, or end, a live phone call. |
