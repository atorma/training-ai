# Spec Delta

## ADDED Requirements

### Requirement: Summary includes plan context

Summary generation SHALL receive, without a model call, the plan pointer for today in the
request timezone, yesterday's and today's plan in full, the next six days in compact form, and
active programmes' recurring items and guidance topics. It SHALL have the read-only plan tools.
It SHALL compare yesterday's plan with recorded activity, attributing differences only to
session descriptions, memory or recorded data, and SHALL NOT guess unstated reasons.

#### Scenario: Planned session done

- **WHEN** yesterday's plan had a 60 min indoor bike session and a matching ride was recorded
- **THEN** the summary reports the session as done

#### Scenario: No matching activity and no known reason

- **WHEN** yesterday had a planned swim and no swim activity was recorded
- **THEN** the summary says no matching activity was found and does not guess why

#### Scenario: No plan covers today

- **WHEN** no block covers today and no programme is active
- **THEN** the summary is generated normally and states that no plan covers today

### Requirement: Informed session suggestions

When the deployment's summary instructions ask for a session suggestion, the summary SHALL
suggest sessions for the requested days with a rationale that weighs the plan or its absence,
key sessions, memory and recorded activity. The summary SHALL NOT change the plan; a suggestion
that departs from the plan SHALL say so.

#### Scenario: Suggestion respects a constraint

- **WHEN** the instructions ask for today's session suggestion, today's plan has a key run, and a constraint forbids running
- **THEN** the summary suggests a non-running alternative and explains it with the constraint and the session's key status

#### Scenario: Suggestion without a plan

- **WHEN** the instructions ask for a suggestion and no block covers today
- **THEN** the summary suggests a session based on memory and recent activity and says no plan covers today

#### Scenario: Instructions do not ask

- **WHEN** the summary instructions do not ask for a session suggestion
- **THEN** the summary follows its instructions and the plan context only informs the comparison with yesterday
