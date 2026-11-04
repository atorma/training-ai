# Spec Delta

## Purpose

Let the user attach files such as PDFs and images to chat messages, store them durably, show and
download them from the transcript, and give the assistant their content without resending file
bytes in every later turn.

## ADDED Requirements

### Requirement: Upload an attachment

`POST /api/attachments` SHALL accept one file as multipart form data and return `201` with an
attachment id, file name, media type, size in bytes and content hash. Accepted media types SHALL
be PDF, PNG, JPEG and WebP, with at most 20 MB per file. Uploading identical bytes again SHALL
reuse the stored content.

#### Scenario: Upload a PDF

- **WHEN** the user uploads a 2 MB file `week-plan.pdf` of type `application/pdf`
- **THEN** the response is `201` with an id, the name, `application/pdf`, the size and a hash

#### Scenario: Unsupported type

- **WHEN** the user uploads a `.zip` file
- **THEN** the response is `400` and nothing is stored

#### Scenario: File too large

- **WHEN** the user uploads a 25 MB PDF
- **THEN** the response is `413` and nothing is stored

#### Scenario: Same file uploaded twice

- **WHEN** the same bytes are uploaded twice under different names
- **THEN** both uploads return attachment ids whose content hash is identical and the bytes are stored once

### Requirement: Removing an unsent upload

`DELETE /api/attachments/{id}` SHALL delete an upload that no message references and return
`204`. Removing a chip from the composer SHALL call it. An attachment already sent with a
message SHALL NOT be deleted; the response is `409`. An unknown id returns `404`.

#### Scenario: Chip removed before sending

- **WHEN** the user uploads `week-plan.pdf`, then removes its chip from the composer
- **THEN** the upload is deleted and sending its id returns `400`

#### Scenario: Attachment already sent

- **WHEN** a delete targets an attachment carried by a sent message
- **THEN** the response is `409` and the message still shows the attachment

### Requirement: Attachments in the transcript

Thread detail SHALL list each user message's attachments (id, name, media type, size), and the
UI SHALL show them as chips on that message. Selecting a chip SHALL download the original file
from `GET /api/attachments/{id}/content` with its original name and media type.

#### Scenario: Reopen a thread on another device

- **WHEN** a thread whose message carried `week-plan.pdf` is opened on another browser
- **THEN** the message shows a `week-plan.pdf` chip that downloads the same bytes

#### Scenario: Unknown attachment

- **WHEN** content is requested for an unknown attachment id
- **THEN** the response is `404`

### Requirement: Attachment content reaches the model in its own turn

The model request for a turn SHALL include the content of that turn's attachments: images as
images and PDFs as documents. A retry of that turn SHALL include them again.

#### Scenario: Ask about a screenshot

- **WHEN** the user sends a screenshot of a workout with "what does this session target?"
- **THEN** the model request for that turn contains the image

#### Scenario: Retry keeps attachments

- **WHEN** a turn carrying a PDF fails and the user retries
- **THEN** the retried model request contains the PDF

### Requirement: Replayed history refers to attachments without content

Earlier turns replayed as history SHALL represent each attachment by a short placeholder naming
its id, file name, media type and size, never by its bytes. Placeholders SHALL count toward the
history budget by their own length.

#### Scenario: Follow-up question

- **WHEN** the user asks a follow-up question in a thread whose earlier message carried a PDF
- **THEN** the model request contains a placeholder for that PDF and not its content

### Requirement: Deleting a thread deletes its attachments

Deleting a thread SHALL remove the attachments its messages carried so that the app no longer
offers them. Content that another message or stored record references SHALL stay available
there. Stored data MAY remain until a cleanup job removes it.

#### Scenario: Delete a thread with an attachment

- **WHEN** a thread whose only message carried `week-plan.pdf` is deleted
- **THEN** downloading that attachment returns `404`

#### Scenario: Content still referenced elsewhere

- **WHEN** the same file was attached in two threads and one thread is deleted
- **THEN** the attachment in the remaining thread still downloads
