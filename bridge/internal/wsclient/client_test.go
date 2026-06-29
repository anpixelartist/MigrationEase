package wsclient

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"

	"github.com/tallymigration/bridge/internal/protocol"
	"github.com/tallymigration/bridge/internal/tally"
)

func TestRelaysPushJobToTally(t *testing.T) {
	// fake Tally gateway
	tallySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("<RESPONSE><CREATED>1</CREATED></RESPONSE>"))
	}))
	defer tallySrv.Close()

	// fake relay: sends one push_job, then reads the bridge's reply
	got := make(chan protocol.JobResult, 1)
	relaySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close(websocket.StatusNormalClosure, "")
		ctx := r.Context()
		_ = wsjson.Write(ctx, c, protocol.PushJob{
			Type:   protocol.TypePushJob,
			JobID:  "j1",
			XMLB64: base64.StdEncoding.EncodeToString([]byte("<ENVELOPE/>")),
		})
		var res protocol.JobResult
		if err := wsjson.Read(ctx, c, &res); err == nil {
			got <- res
		}
	}))
	defer relaySrv.Close()

	wsURL := "ws" + strings.TrimPrefix(relaySrv.URL, "http") + "/bridge/ws"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	go func() {
		_ = connectAndServe(ctx, Config{RelayURL: wsURL, APIKey: "k", Tally: tally.New(tallySrv.URL)})
	}()

	select {
	case res := <-got:
		if res.Type != protocol.TypeJobResult || !strings.Contains(res.TallyXML, "CREATED") {
			t.Fatalf("unexpected job result: %+v", res)
		}
	case <-ctx.Done():
		t.Fatal("timed out waiting for the bridge to relay the job")
	}
}
