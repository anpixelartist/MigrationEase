package tally

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPostSendsBodyAndReturnsResponse(t *testing.T) {
	var gotBody []byte
	var gotContentType string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotBody, _ = io.ReadAll(r.Body)
		gotContentType = r.Header.Get("Content-Type")
		_, _ = w.Write([]byte("<RESPONSE><CREATED>1</CREATED></RESPONSE>"))
	}))
	defer srv.Close()

	client := New(srv.URL)
	resp, err := client.Post(context.Background(), []byte("<ENVELOPE>hi</ENVELOPE>"))
	if err != nil {
		t.Fatalf("Post returned error: %v", err)
	}
	if string(gotBody) != "<ENVELOPE>hi</ENVELOPE>" {
		t.Errorf("server got body %q", gotBody)
	}
	if gotContentType != "text/xml" {
		t.Errorf("content-type = %q, want text/xml", gotContentType)
	}
	if string(resp) != "<RESPONSE><CREATED>1</CREATED></RESPONSE>" {
		t.Errorf("unexpected response %q", resp)
	}
}

func TestPostNon200IsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer srv.Close()

	if _, err := New(srv.URL).Post(context.Background(), []byte("x")); err == nil {
		t.Fatal("expected error on HTTP 500, got nil")
	}
}

func TestNewDefaultsBaseURL(t *testing.T) {
	if New("").BaseURL != DefaultBaseURL {
		t.Errorf("empty baseURL should default to %s", DefaultBaseURL)
	}
}
