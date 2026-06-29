// Package relay executes push jobs received from the cloud against the local Tally gateway.
//
// The handler enforces the bridge's safety guarantees (plan §7): just-in-time active-company check,
// artifact integrity (sha256) verification before anything touches Tally, and idempotency by job_id
// so a reconnect/replay never double-imports.
package relay

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"sync"
	"time"

	"github.com/tallymigration/bridge/internal/protocol"
)

// Fetcher retrieves the generated XML artifact for a (presigned) URL.
type Fetcher func(ctx context.Context, url string) ([]byte, error)

// TallyPoster posts XML to Tally and returns the raw response.
type TallyPoster interface {
	Post(ctx context.Context, xml []byte) ([]byte, error)
}

// CompanyProvider returns the currently-active Tally company GUID ("" if unknown).
type CompanyProvider func() string

// Handler processes push jobs. It is safe for concurrent use.
type Handler struct {
	fetch      Fetcher
	tally      TallyPoster
	activeGUID CompanyProvider

	mu   sync.Mutex
	done map[string]protocol.JobResult // completed jobs, for idempotent replay
}

// NewHandler wires the dependencies. activeGUID may be nil to skip the company guard.
func NewHandler(fetch Fetcher, tally TallyPoster, activeGUID CompanyProvider) *Handler {
	return &Handler{fetch: fetch, tally: tally, activeGUID: activeGUID, done: make(map[string]protocol.JobResult)}
}

// Handle executes one push job. Exactly one of (result, jobErr) is non-nil.
func (h *Handler) Handle(ctx context.Context, job protocol.PushJob) (*protocol.JobResult, *protocol.JobError) {
	// Idempotency: a replay of an already-completed job returns the cached result without re-importing.
	h.mu.Lock()
	if cached, ok := h.done[job.JobID]; ok {
		h.mu.Unlock()
		result := cached
		return &result, nil
	}
	h.mu.Unlock()

	// Just-in-time company guard: refuse to import into the wrong open company.
	if job.CompanyGUID != "" && h.activeGUID != nil {
		if active := h.activeGUID(); active != "" && active != job.CompanyGUID {
			return nil, jobError(job.JobID, protocol.ReasonCompanyMismatch)
		}
	}

	xml, err := h.fetch(ctx, job.XMLURL)
	if err != nil {
		return nil, jobError(job.JobID, protocol.ReasonFetchFailed)
	}

	// Artifact integrity: the bytes we're about to import must match what the cloud issued.
	if job.XMLSHA256 != "" {
		sum := sha256.Sum256(xml)
		if hex.EncodeToString(sum[:]) != job.XMLSHA256 {
			return nil, jobError(job.JobID, protocol.ReasonHashMismatch)
		}
	}

	start := time.Now()
	resp, err := h.tally.Post(ctx, xml)
	if err != nil {
		return nil, jobError(job.JobID, protocol.ReasonTallyUnreachable)
	}

	result := protocol.JobResult{
		Type:       protocol.TypeJobResult,
		JobID:      job.JobID,
		HTTPStatus: 200,
		TallyXML:   string(resp),
		DurationMS: time.Since(start).Milliseconds(),
	}
	h.mu.Lock()
	h.done[job.JobID] = result
	h.mu.Unlock()
	return &result, nil
}

func jobError(jobID, reason string) *protocol.JobError {
	return &protocol.JobError{Type: protocol.TypeJobError, JobID: jobID, Reason: reason}
}
