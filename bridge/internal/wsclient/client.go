// Package wsclient is the bridge's outbound WSS client: it dials the cloud relay, receives push jobs,
// relays the XML to the local Tally gateway, and returns the response. It reconnects with backoff.
package wsclient

import (
	"context"
	"encoding/base64"
	"fmt"
	"math/rand"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/tallymigration/bridge/internal/protocol"
	"github.com/tallymigration/bridge/internal/tally"
)

const maxMessageBytes = 64 * 1024 * 1024 // generous cap for large master imports

type Config struct {
	RelayURL string // e.g. wss://relay.example.com/bridge/ws
	APIKey   string // bk_...
	Tally    *tally.Client
	Company  string // active company (for heartbeats)
}

// Run maintains a connection to the relay until ctx is cancelled, reconnecting with backoff+jitter.
func Run(ctx context.Context, cfg Config) error {
	backoff := time.Second
	for ctx.Err() == nil {
		if err := connectAndServe(ctx, cfg); err != nil && ctx.Err() == nil {
			fmt.Println("bridge: connection lost:", err)
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		wait := backoff + time.Duration(rand.Int63n(int64(time.Second)))
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(wait):
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
	return ctx.Err()
}

func connectAndServe(ctx context.Context, cfg Config) error {
	conn, _, err := websocket.Dial(ctx, cfg.RelayURL+"?key="+cfg.APIKey, nil)
	if err != nil {
		return err
	}
	defer conn.Close(websocket.StatusNormalClosure, "")
	conn.SetReadLimit(maxMessageBytes)

	hbCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go heartbeat(hbCtx, conn, cfg)

	for {
		var job protocol.PushJob
		if err := wsjson.Read(ctx, conn, &job); err != nil {
			return err
		}
		if job.Type == protocol.TypePushJob {
			handlePush(ctx, conn, cfg, job)
		}
	}
}

func handlePush(ctx context.Context, conn *websocket.Conn, cfg Config, job protocol.PushJob) {
	xml, err := base64.StdEncoding.DecodeString(job.XMLB64)
	if err != nil {
		_ = wsjson.Write(ctx, conn, protocol.JobError{Type: protocol.TypeJobError, JobID: job.JobID, Reason: "bad_payload"})
		return
	}
	resp, err := cfg.Tally.Post(ctx, xml)
	if err != nil {
		_ = wsjson.Write(ctx, conn, protocol.JobError{
			Type: protocol.TypeJobError, JobID: job.JobID, Reason: protocol.ReasonTallyUnreachable,
		})
		return
	}
	_ = wsjson.Write(ctx, conn, protocol.JobResult{
		Type: protocol.TypeJobResult, JobID: job.JobID, HTTPStatus: 200, TallyXML: string(resp),
	})
}

func heartbeat(ctx context.Context, conn *websocket.Conn, cfg Config) {
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			up := true
			if _, err := cfg.Tally.Probe(ctx); err != nil {
				up = false
			}
			_ = wsjson.Write(ctx, conn, protocol.Heartbeat{
				Type: protocol.TypeHeartbeat, TallyUp: up, Company: cfg.Company,
			})
		}
	}
}
