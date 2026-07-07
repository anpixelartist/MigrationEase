package wsclient

import (
	"context"
	"encoding/base64"
	"io"
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

// The company selected by the BACKEND (payload SVCURRENTCOMPANY) must win over the CLI --company
// flag, or pushes land in the wrong Tally company.
func TestHonorsPayloadCompanyOverFlag(t *testing.T) {
	received := make(chan string, 4)
	tallySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		received <- string(body)
		_, _ = w.Write([]byte("<RESPONSE><CREATED>1</CREATED></RESPONSE>"))
	}))
	defer tallySrv.Close()

	payload := `<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>` +
		`<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME><STATICVARIABLES>` +
		`<SVCURRENTCOMPANY>PayloadCo</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>` +
		`<REQUESTDATA><TALLYMESSAGE><LEDGER NAME="X"><NAME>X</NAME></LEDGER></TALLYMESSAGE></REQUESTDATA>` +
		`</IMPORTDATA></BODY></ENVELOPE>`

	done := make(chan protocol.JobResult, 1)
	relaySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close(websocket.StatusNormalClosure, "")
		ctx := r.Context()
		_ = wsjson.Write(ctx, c, protocol.PushJob{
			Type: protocol.TypePushJob, JobID: "j1",
			XMLB64: base64.StdEncoding.EncodeToString([]byte(payload)),
		})
		var res protocol.JobResult
		if err := wsjson.Read(ctx, c, &res); err == nil {
			done <- res
		}
	}))
	defer relaySrv.Close()

	wsURL := "ws" + strings.TrimPrefix(relaySrv.URL, "http") + "/bridge/ws"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() {
		// CLI flag says "FlagCo" — the payload's "PayloadCo" must win.
		_ = connectAndServe(ctx, Config{RelayURL: wsURL, APIKey: "k", Company: "FlagCo", Tally: tally.New(tallySrv.URL)})
	}()

	select {
	case <-done:
	case <-ctx.Done():
		t.Fatal("timed out waiting for relay")
	}
	select {
	case body := <-received:
		if !strings.Contains(body, "<SVCURRENTCOMPANY>PayloadCo</SVCURRENTCOMPANY>") {
			t.Fatalf("expected payload company PayloadCo in Tally request, got: %s", body)
		}
		if strings.Contains(body, "FlagCo") {
			t.Fatalf("CLI flag company FlagCo leaked into the Tally request: %s", body)
		}
	default:
		t.Fatal("Tally received no request")
	}
}

// With --test-company set, a push aimed at any other company must be refused before touching Tally.
func TestSandboxGateBlocksOtherCompany(t *testing.T) {
	tallyHit := make(chan bool, 1)
	tallySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tallyHit <- true
		_, _ = w.Write([]byte("<RESPONSE><CREATED>1</CREATED></RESPONSE>"))
	}))
	defer tallySrv.Close()

	payload := `<ENVELOPE><BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>` +
		`<STATICVARIABLES><SVCURRENTCOMPANY>ProdCo</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>` +
		`<REQUESTDATA><TALLYMESSAGE><LEDGER NAME="X"><NAME>X</NAME></LEDGER></TALLYMESSAGE></REQUESTDATA>` +
		`</IMPORTDATA></BODY></ENVELOPE>`

	gotReason := make(chan string, 1)
	relaySrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close(websocket.StatusNormalClosure, "")
		ctx := r.Context()
		_ = wsjson.Write(ctx, c, protocol.PushJob{
			Type: protocol.TypePushJob, JobID: "j1",
			XMLB64: base64.StdEncoding.EncodeToString([]byte(payload)),
		})
		var frame map[string]any
		if err := wsjson.Read(ctx, c, &frame); err == nil {
			if reason, ok := frame["reason"].(string); ok {
				gotReason <- reason
			}
		}
	}))
	defer relaySrv.Close()

	wsURL := "ws" + strings.TrimPrefix(relaySrv.URL, "http") + "/bridge/ws"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() {
		_ = connectAndServe(ctx, Config{RelayURL: wsURL, APIKey: "k", TestCompany: "TestCo", Tally: tally.New(tallySrv.URL)})
	}()

	select {
	case reason := <-gotReason:
		if reason != "sandbox_blocked" {
			t.Fatalf("expected sandbox_blocked, got %q", reason)
		}
	case <-ctx.Done():
		t.Fatal("timed out")
	}
	select {
	case <-tallyHit:
		t.Fatal("Tally was called despite the sandbox gate")
	default:
	}
}

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
			XMLB64: base64.StdEncoding.EncodeToString([]byte("<ENVELOPE><TALLYMESSAGE>Some Payload</TALLYMESSAGE></ENVELOPE>")),
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
