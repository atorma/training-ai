# Spec Delta

## Purpose

Turn coach-provided documents into a distilled, queryable plan: dated training blocks and
supplementary programmes that hold the user's current training, which the assistant and summary
use and the user and assistant keep up to date.

## ADDED Requirements

### Requirement: Blocks and programmes

The system SHALL store two kinds of plan, each with a title and an inclusive date range. A
block is a main plan with dated sessions; blocks SHALL NOT overlap each other. A programme is a
supplementary plan (for example a gym programme or seasonal guidance) without dated sessions;
programmes MAY overlap blocks and each other. A plan SHALL keep its source documents for
download.

#### Scenario: Gym programme across two blocks

- **WHEN** a gym programme covers 2030-10-20 to 2030-12-31 and blocks cover 2030-10-20 to 2030-11-16
  and 2030-11-17 to 2030-12-14
- **THEN** both blocks and the programme are stored, and a date in either block also has the programme active

#### Scenario: Off-season guidance without a block

- **WHEN** a programme with weekly targets covers 2030-09-01 to 2030-10-18 and no block covers those dates
- **THEN** the programme is the active plan for those dates

### Requirement: Distilled plan content

Plans SHALL hold distilled content, not the document's layout, in the document's language.
Both kinds SHALL have named workouts, recurring items (habits whose text states how often),
guidance (rules, advice, upcoming events) and open questions. A block SHALL also have a phase,
a focus, weeks with a label and hours range, and dated sessions with a title, key flag,
optional time and place, and options made of parts, each with a sport, optional duration and
workout, and instructions.

#### Scenario: Session refers to a workout

- **WHEN** the plan says "Tue: swim program no. 2" and defines program 2 elsewhere in the document
- **THEN** the Tuesday session has a swim part linked to the block's workout "Swim program 2"

#### Scenario: Workout numbers are per plan

- **WHEN** two blocks both define "Swim program 1" with different content
- **THEN** each block's sessions link to their own block's workout

#### Scenario: Part instructions

- **WHEN** a brick says "easy bike with a few short accelerations late in the ride, then an easy run with 2 x 20 s
  at race pace"
- **THEN** the bike part and the run part each carry their own instructions

#### Scenario: Brick and double day

- **WHEN** a day says "bike 90 min, quick transition, run 30 min" and another day says "morning: swim, evening: run"
- **THEN** the first day has one session whose option has a bike part and a run part, and the second day has
  two sessions

#### Scenario: Coach's alternatives

- **WHEN** the plan says "Sun: bike 2h easy, or 90 min bike + 30 min run, or pole walking"
- **THEN** the Sunday session has three options, the second with two parts, and none is chosen

#### Scenario: Rest as an option

- **WHEN** the plan says "Thu: rest, or swim program 4"
- **THEN** the Thursday session has a rest option without parts and a swim option

#### Scenario: Habit with a frequency

- **WHEN** the plan asks for foot strengthening exercises 2–3 times a week
- **THEN** the plan has a recurring item whose text keeps "2–3 times a week", linked to the foot exercise workout

#### Scenario: Habit tied to other sessions

- **WHEN** the plan asks for a short mobility routine before every run
- **THEN** the recurring item's text says "before every run" and no count is invented

### Requirement: Sessions use programme workouts

A session part or recurring item MAY link to a workout of a programme active on its date
instead of a copy in its own plan. Deleting the programme SHALL clear such links. Re-extracting
the programme SHALL keep workouts whose names are unchanged, and SHALL clear links to removed
workouts with an open question in the linking plan. Removing a workout by an edit SHALL do the
same. A plan that loses a link SHALL get a new version.

#### Scenario: Gym session from the gym programme

- **WHEN** a block says "Wed: gym, workout B" and an active gym programme defines "Workout B"
- **THEN** Wednesday's strength part links to the programme's "Workout B" and the block has no copy of it

#### Scenario: Programme deleted

- **WHEN** the gym programme is deleted
- **THEN** Wednesday's session remains with its part's instructions and no workout link

### Requirement: Variants follow the user's targets

When the document gives variants for different groups of athletes (for example by target race
distance), the extraction SHALL keep only the user's variant, determined from memory, the
external training calendar or the user's answer. Options that depend on the day (such as
"or rest if tired") SHALL be kept. A variant that cannot be determined SHALL stay with its
condition and an open question.

#### Scenario: Target known from memory

- **WHEN** a goal in memory says the user targets a half-distance race and Sunday has an "IM group" and a "half and
  quarter group" variant
- **THEN** Sunday keeps only the half and quarter variant

#### Scenario: Target unknown

- **WHEN** neither memory nor the calendar tells which variant applies
- **THEN** the session keeps both variants with their conditions, an open question is recorded, and the reply asks
  the user

### Requirement: Races are not planned sessions

Races, camps and volunteering SHALL NOT be stored as sessions, since participation is
individual. Where the document offers a race or an equivalent session, the session SHALL be the
equivalent training, and the race SHALL be recorded as upcoming-event guidance. If the user's
memory or external calendar shows a race that day, the extraction SHALL record an open question
for that day instead of guessing. Tests and group sessions are sessions.

#### Scenario: Race or equivalent

- **WHEN** Saturday says "club 10 km road race, or the equivalent tempo run below"
- **THEN** Saturday's session is the tempo run and the race appears as upcoming-event guidance

#### Scenario: User races that day

- **WHEN** the external calendar shows the user racing on that Saturday
- **THEN** an open question asks how to plan that day, and the reply raises it

#### Scenario: Group session

- **WHEN** Sunday has a group brick session at 10:00 in a named park
- **THEN** Sunday's session records the time and place

### Requirement: Extract a plan from an attachment

When the user attaches a coach document in chat and asks to use it, the assistant SHALL infer
whether it is a block or a programme and its date range, ask only when unclear, extract it
within that chat run and save it immediately. The reply SHALL summarize the plan (week by week
for blocks) and list open questions. Documents over 40 pages SHALL be rejected.

#### Scenario: New four-week block

- **WHEN** the user attaches `block-3.pdf` covering 2030-03-04 to 2030-03-31 and says "here's my new plan"
- **THEN** a block with that range is saved and the reply shows each week's sessions and any open questions

#### Scenario: Programme without an end date

- **WHEN** the user attaches a gym programme labelled "autumn onwards"
- **THEN** the assistant asks for the end date before saving the programme

#### Scenario: Document too long

- **WHEN** the attached document has 120 pages
- **THEN** nothing is extracted and the reply states the 40-page limit

#### Scenario: Retried run after saving

- **WHEN** the run that saved a block fails afterwards and the user retries it
- **THEN** the block is not saved a second time and the reply refers to the saved block

#### Scenario: Extraction fails

- **WHEN** the model cannot produce a valid extraction
- **THEN** no plan is saved and the run fails visibly so the user can retry

### Requirement: Overlapping blocks are rejected

A block's range SHALL exclude previews of the next block. An extraction, re-extraction or edit
that would make two blocks overlap SHALL save nothing, and the reply SHALL name the conflicting
block and its range. Overlapping blocks SHALL NOT be trimmed or replaced automatically.

#### Scenario: Preview of the next block

- **WHEN** a block covering 2030-03-04 to 2030-03-31 ends with a preview of the first week of April
- **THEN** the block's range ends on 2030-03-31 and the preview is stored as upcoming guidance

#### Scenario: Overlapping block

- **WHEN** a new block covers 2030-03-25 to 2030-04-21 and a block already covers 2030-03-04 to 2030-03-31
- **THEN** nothing is saved and the reply names the earlier block and its range

### Requirement: Extraction is validated before saving

Saved content SHALL satisfy: session dates lie within the block, weeks start on a Monday that
overlaps the block, every session has an option, every workout link points to a workout of the
same plan or of a programme active on the linking date, duration ranges are ordered, and
programmes have no sessions or weeks. An option without parts means rest. Violations that
cannot be repaired SHALL become open questions rather than be dropped silently.

#### Scenario: Session outside the range

- **WHEN** an extracted session is dated one week after the block's end
- **THEN** it is not saved as a session and an open question records it

### Requirement: Unknown sports in an extraction

A sport outside the known sports SHALL be saved as `other` with an open question naming it, so
that one unknown sport does not lose the extraction.

#### Scenario: Unknown sport

- **WHEN** an extracted session has the sport "rowing"
- **THEN** it is saved with the sport `other` and an open question names "rowing"

### Requirement: Editing the plan

The assistant SHALL edit plan content directly in chat to fix extraction errors and to
adapt, move, add (including on rest days) or remove sessions and to change weeks, only when the
user asks or accepts the assistant's proposal. Edits
SHALL be validated like extractions, rejected if the plan changed since it was read, and
applied once even when the run is retried. Descriptions MAY state an adaptation's purpose
naturally but SHALL NOT carry audit notes.

#### Scenario: Wrong program number

- **WHEN** the user says "Saturday should be swim program 2, not program 1"
- **THEN** the Saturday swim part links to program 2 and no correction note is added

#### Scenario: Session moved

- **WHEN** the user says "my Tuesday swims are on Thursday evening" for the current week
- **THEN** the swim session's date becomes Thursday and Tuesday no longer lists it

#### Scenario: Gym session on a rest day

- **WHEN** the user asks to add a gym session on Wednesday, which the plan has as a rest day
- **THEN** Wednesday lists the gym session

#### Scenario: Adapted session

- **WHEN** the user accepts the proposal to replace Tuesday's run intervals with indoor bike intervals because
  of a sore ankle
- **THEN** the Tuesday session describes the bike intervals, for example "60 min indoor bike intervals that avoid
  loading the sore ankle"

#### Scenario: Open question answered

- **WHEN** the user answers which variant applies to Sunday
- **THEN** Sunday keeps that variant and the open question is removed

#### Scenario: Concurrent edit

- **WHEN** another thread edited the plan after the assistant read it
- **THEN** the edit is rejected and the assistant reads the plan again before retrying

#### Scenario: Week label corrected

- **WHEN** the user says "week 3 is a recovery week with 6 to 8 hours"
- **THEN** week 3's label and hours range change

#### Scenario: Retried edit

- **WHEN** a run that moved a session fails after the edit was saved and is retried
- **THEN** the session is moved only once

### Requirement: Re-extracting a plan

The user SHALL be able to ask for a plan to be extracted again from its source documents. The
new extraction SHALL replace the plan's content, and the assistant SHALL warn beforehand that
all edits made to the plan are discarded. If the plan changed after re-extraction started,
nothing SHALL be replaced.

#### Scenario: Poor first extraction

- **WHEN** the user says "the extraction missed half of week 2, redo it" and confirms the warning
- **THEN** the block's content is replaced by a new extraction from the same sources

#### Scenario: Edited during re-extraction

- **WHEN** another thread edits the block while its re-extraction runs
- **THEN** the re-extraction saves nothing and reports the conflict

### Requirement: Plan API and page

`GET /api/plans` SHALL list blocks and programmes with kind, title and date range, and
`GET /api/plans/{id}` SHALL return a plan's full content. The plan page SHALL show a block's
current sessions by week with key sessions marked and week labels, the programmes active during
it, and each plan's workouts, recurring items, guidance by topic, open questions and source
files to download. It SHALL NOT distinguish edited content from extracted content.

#### Scenario: Review the current plan

- **WHEN** the user opens the plan page for the current block after moving a swim to Thursday
- **THEN** the week lists the swim on Thursday with its key flag and nothing on Tuesday, and the active gym programme
  is shown

#### Scenario: Unknown plan

- **WHEN** `GET /api/plans/{id}` references an unknown plan
- **THEN** the response is `404`

### Requirement: Plan page stays current

Every committed plan write SHALL publish the app's existing change event. An open plan page or
plan list SHALL show the change without a reload, keeping the current content visible while it
refreshes.

#### Scenario: Edited on another device

- **WHEN** the plan page is open on one device and the user moves Tuesday's swim to Thursday in chat on another
- **THEN** the open page lists the swim on Thursday without a reload

#### Scenario: Deleted on another device

- **WHEN** the open plan's block is deleted from another device
- **THEN** the page says the plan was deleted and links to the plan list

### Requirement: Deleting a plan

`DELETE /api/plans/{id}` SHALL delete the plan and its content. Its source attachments SHALL
remain while their conversation messages still reference them. Shared memory SHALL be
unaffected.

#### Scenario: Delete a block

- **WHEN** the user deletes a block from the plan page
- **THEN** it no longer appears in the plan list, programmes are unchanged and memory entries are unchanged

### Requirement: Plan sources outlive their conversation

A plan's source documents SHALL remain downloadable from the plan page after the conversation
that carried them is deleted.

#### Scenario: Thread with the plan PDF deleted

- **WHEN** the thread in which `block-3.pdf` was attached is deleted
- **THEN** the block still offers `block-3.pdf` for download
