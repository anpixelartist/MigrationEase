// Package tally is a thin HTTP client for Tally's XML gateway (default http://127.0.0.1:9000).
//
// Tally accepts an XML request body over POST and returns an XML response body. This client makes
// no assumptions about the request/response shapes — building requests and parsing responses lives
// in the cloud backend; the bridge only relays bytes.
package tally

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"time"
)

// DefaultBaseURL is Tally's loopback gateway. The gateway must be enabled in Tally and bound to
// localhost; the bridge is its only client.
const DefaultBaseURL = "http://127.0.0.1:9000"

// companyProbeRequest is a read-only export of open companies — used to check the gateway is alive.
const companyProbeRequest = `<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>Company</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES><TDL><TDLMESSAGE><COLLECTION NAME="Company" ISMODIFY="No"><TYPE>Company</TYPE><NATIVEMETHOD>Name</NATIVEMETHOD></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>`

// Client talks to a Tally gateway.
type Client struct {
	BaseURL string
	HTTP    *http.Client
}

// New returns a Client for baseURL (DefaultBaseURL when empty) with a sane timeout.
func New(baseURL string) *Client {
	if baseURL == "" {
		baseURL = DefaultBaseURL
	}
	return &Client{BaseURL: baseURL, HTTP: &http.Client{Timeout: 60 * time.Second}}
}

// Post sends a raw Tally XML request and returns the raw response body. A non-200 status is
// returned as an error, with whatever body was read still provided to the caller.
func (c *Client) Post(ctx context.Context, xml []byte) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL, bytes.NewReader(xml))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "text/xml")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("tally request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading tally response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return body, fmt.Errorf("tally returned HTTP %d", resp.StatusCode)
	}
	return body, nil
}

// Probe checks the gateway is reachable, returning the (small) company-list response body.
func (c *Client) Probe(ctx context.Context) ([]byte, error) {
	return c.Post(ctx, []byte(companyProbeRequest))
}
