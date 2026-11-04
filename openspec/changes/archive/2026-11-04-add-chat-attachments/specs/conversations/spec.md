# Spec Delta

## MODIFIED Requirements

### Requirement: Sending a message starts a server-owned run

Sending `{"text": ..., "attachment_ids": [...]}` SHALL commit the user message, its attachment
references and a new `running` run atomically and return `202` with the run. `attachment_ids` is
optional (at most 5 ids of uploads not yet sent); text MAY be empty when at least one attachment
is present. Generation SHALL continue independently of any browser connection; closing the
browser SHALL NOT cancel it. On success the new messages and `completed` status SHALL be
committed together.

#### Scenario: Message accepted

- **WHEN** the user sends a non-empty message of at most 1,000,000 characters to an idle thread
- **THEN** the response is `202` with a run whose status is `running`

#### Scenario: Attachment-only message accepted

- **WHEN** the user sends empty text with one uploaded PDF to an idle thread
- **THEN** the response is `202` and the saved message shows the PDF

#### Scenario: Invalid message

- **WHEN** the message is malformed, longer than 1,000,000 characters, or has empty text and no
  attachments
- **THEN** the response is `400` and nothing is saved

#### Scenario: Invalid attachment reference

- **WHEN** a message references an unknown attachment id, one already sent, or more than 5 ids
- **THEN** the response is `400` and nothing is saved

#### Scenario: Concurrent run blocked

- **WHEN** a send, retry or delete targets a thread whose latest run is still running
- **THEN** the response is `409`
