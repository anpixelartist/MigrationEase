// Package protocol defines the JSON frames exchanged between the cloud relay and the bridge over WSS.
package protocol

// Frame type discriminators (the "type" field).
const (
	TypePushJob   = "push_job"
	TypeJobAck    = "job_ack"
	TypeJobResult = "job_result"
	TypeJobError  = "job_error"
	TypeHeartbeat = "heartbeat"
)

// JobError reasons.
const (
	ReasonFetchFailed      = "fetch_failed"
	ReasonHashMismatch     = "hash_mismatch"
	ReasonCompanyMismatch  = "company_mismatch"
	ReasonTallyUnreachable = "tally_unreachable"
)

// PushJob is sent cloud -> bridge to request an import. The XML is fetched from XMLURL (a presigned
// URL) and verified against XMLSHA256 before being POSTed to the Tally company identified by CompanyGUID.
type PushJob struct {
	Type        string `json:"type"`
	JobID       string `json:"job_id"`
	CompanyGUID string `json:"company_guid"`
	// Either a presigned URL + hash (large artefacts) or inline base64 XML (the current relay MVP).
	XMLURL    string `json:"xml_url,omitempty"`
	XMLSHA256 string `json:"xml_sha256,omitempty"`
	XMLB64    string `json:"xml_b64,omitempty"`
}

// JobAck is sent bridge -> cloud immediately on receiving a job.
type JobAck struct {
	Type  string `json:"type"`
	JobID string `json:"job_id"`
}

// JobResult is sent bridge -> cloud with Tally's raw response.
type JobResult struct {
	Type       string `json:"type"`
	JobID      string `json:"job_id"`
	HTTPStatus int    `json:"http_status"`
	TallyXML   string `json:"tally_xml"`
	DurationMS int64  `json:"duration_ms"`
}

// JobError is sent bridge -> cloud when a job cannot be completed.
type JobError struct {
	Type   string `json:"type"`
	JobID  string `json:"job_id"`
	Reason string `json:"reason"`
}

// Heartbeat is sent bridge -> cloud periodically with Tally liveness + the active company.
type Heartbeat struct {
	Type        string `json:"type"`
	TallyUp     bool   `json:"tally_up"`
	Company     string `json:"company"`
	CompanyGUID string `json:"company_guid"`
}
