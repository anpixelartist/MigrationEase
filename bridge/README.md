# TallyMigration Bridge (Go)

The local Windows agent that relays generated Tally XML from the cloud to the user's TallyPrime
gateway on `http://127.0.0.1:9000`. It dials **out** to the cloud (no inbound firewall holes).

## Status (first slice — stdlib only, compiles offline)

| Package | What | Tested |
|---|---|---|
| `internal/tally` | HTTP client for Tally's XML gateway (POST request → raw response) | unit + live (`bridge probe`) |
| `internal/protocol` | JSON frame types for the cloud↔bridge relay protocol | — |
| `internal/relay` | Job handler: fetch artifact → **verify sha256** → **active-company guard** → POST to Tally → idempotent `JobResult` | unit |
| `cmd/bridge` | CLI: `probe` (test the live Tally gateway), `version` | — |

## Not yet built (need the cloud relay server first)
- Outbound **WSS** client + reconnect supervisor (`coder/websocket`)
- Device **pairing** + API key in **Windows Credential Manager** (`danieljoos/wincred`)
- **Windows service** wrapper (`kardianos/service`) + tray
- Tally **master export** snapshot job

## Dev
```
go -C bridge build ./...
go -C bridge test ./...
go -C bridge run ./cmd/bridge probe          # hits http://127.0.0.1:9000
```
