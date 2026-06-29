package relay

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"testing"

	"github.com/tallymigration/bridge/internal/protocol"
)

// fakeTally records calls and returns a canned response (or error).
type fakeTally struct {
	calls int
	resp  []byte
	err   error
}

func (f *fakeTally) Post(_ context.Context, _ []byte) ([]byte, error) {
	f.calls++
	return f.resp, f.err
}

func sha(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func fetcherFor(xml []byte) Fetcher {
	return func(_ context.Context, _ string) ([]byte, error) { return xml, nil }
}

func TestHandleHappyPath(t *testing.T) {
	xml := []byte("<ENVELOPE>import</ENVELOPE>")
	tally := &fakeTally{resp: []byte("<RESPONSE><CREATED>2</CREATED></RESPONSE>")}
	h := NewHandler(fetcherFor(xml), tally, func() string { return "GUID-1" })

	job := protocol.PushJob{JobID: "j1", CompanyGUID: "GUID-1", XMLURL: "u", XMLSHA256: sha(xml)}
	res, jerr := h.Handle(context.Background(), job)
	if jerr != nil {
		t.Fatalf("unexpected job error: %+v", jerr)
	}
	if res.TallyXML != "<RESPONSE><CREATED>2</CREATED></RESPONSE>" {
		t.Errorf("unexpected tally xml: %q", res.TallyXML)
	}
	if tally.calls != 1 {
		t.Errorf("tally called %d times, want 1", tally.calls)
	}
}

func TestHandleHashMismatch(t *testing.T) {
	xml := []byte("<ENVELOPE>import</ENVELOPE>")
	tally := &fakeTally{resp: []byte("ok")}
	h := NewHandler(fetcherFor(xml), tally, nil)

	job := protocol.PushJob{JobID: "j1", XMLURL: "u", XMLSHA256: "deadbeef"}
	res, jerr := h.Handle(context.Background(), job)
	if res != nil || jerr == nil || jerr.Reason != protocol.ReasonHashMismatch {
		t.Fatalf("want hash_mismatch error, got res=%v err=%v", res, jerr)
	}
	if tally.calls != 0 {
		t.Error("Tally must not be called when the hash mismatches")
	}
}

func TestHandleCompanyMismatch(t *testing.T) {
	xml := []byte("x")
	tally := &fakeTally{resp: []byte("ok")}
	h := NewHandler(fetcherFor(xml), tally, func() string { return "ACTIVE-OTHER" })

	job := protocol.PushJob{JobID: "j1", CompanyGUID: "GUID-WANTED", XMLURL: "u", XMLSHA256: sha(xml)}
	res, jerr := h.Handle(context.Background(), job)
	if res != nil || jerr == nil || jerr.Reason != protocol.ReasonCompanyMismatch {
		t.Fatalf("want company_mismatch error, got res=%v err=%v", res, jerr)
	}
	if tally.calls != 0 {
		t.Error("Tally must not be called on company mismatch")
	}
}

func TestHandleTallyUnreachable(t *testing.T) {
	xml := []byte("x")
	tally := &fakeTally{err: errors.New("connection refused")}
	h := NewHandler(fetcherFor(xml), tally, nil)

	job := protocol.PushJob{JobID: "j1", XMLURL: "u", XMLSHA256: sha(xml)}
	_, jerr := h.Handle(context.Background(), job)
	if jerr == nil || jerr.Reason != protocol.ReasonTallyUnreachable {
		t.Fatalf("want tally_unreachable, got %v", jerr)
	}
}

func TestHandleIdempotentReplay(t *testing.T) {
	xml := []byte("x")
	tally := &fakeTally{resp: []byte("<RESPONSE/>")}
	h := NewHandler(fetcherFor(xml), tally, nil)
	job := protocol.PushJob{JobID: "dup", XMLURL: "u", XMLSHA256: sha(xml)}

	if _, jerr := h.Handle(context.Background(), job); jerr != nil {
		t.Fatalf("first call errored: %v", jerr)
	}
	if _, jerr := h.Handle(context.Background(), job); jerr != nil {
		t.Fatalf("replay errored: %v", jerr)
	}
	if tally.calls != 1 {
		t.Errorf("Tally called %d times on replay, want 1 (idempotent)", tally.calls)
	}
}
