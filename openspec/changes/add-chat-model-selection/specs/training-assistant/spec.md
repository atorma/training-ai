# Spec Delta

## ADDED Requirements

### Requirement: Model configuration is validated at startup

The server SHALL refuse to start when `CHAT_MODELS` is malformed or when the default or any
listed model cannot be set up (unknown provider or model, or missing credentials), naming the
offending entry without revealing credentials.

#### Scenario: Duplicate shown name

- **WHEN** `CHAT_MODELS` is `openai:gpt-6.1-sol,openai-chat:gpt-6.1-sol`
- **THEN** startup fails with an error naming both models, which would both be shown as
  `gpt-6.1-sol`

#### Scenario: Listed model without credentials

- **WHEN** `CHAT_MODELS` lists an Anthropic model and no Anthropic API key is configured
- **THEN** startup fails with an error naming that model

### Requirement: Selectable chat models

`CHAT_MODELS` SHALL list the chat models as comma-separated provider-prefixed model ids. Each
model SHALL be shown by its name within the provider, the id without its provider prefix; ids
and shown names SHALL each be unique. The default model `MODEL` SHALL always be selectable: when
it is not listed it SHALL be added first. Without `CHAT_MODELS` the default is the only model.

#### Scenario: Default not listed

- **WHEN** `MODEL` is `openai:gpt-6.1-sol` and `CHAT_MODELS` is `anthropic:claude-opus-5-5`
- **THEN** the selectable models are `openai:gpt-6.1-sol`, shown as `gpt-6.1-sol`, and
  `anthropic:claude-opus-5-5`, shown as `claude-opus-5-5`

#### Scenario: Two models from one provider

- **WHEN** `CHAT_MODELS` is `anthropic:claude-opus-5-5,anthropic:claude-sonnet-5-5`
- **THEN** both models are selectable and shown as `claude-opus-5-5` and `claude-sonnet-5-5`

#### Scenario: No list configured

- **WHEN** `CHAT_MODELS` is unset
- **THEN** the only selectable model is `MODEL`

### Requirement: Models endpoint

`GET /api/models` SHALL return `{"default": <id>, "models": [{"id": <id>, "label": <name>}]}` in
configured order, with the default first when it was added automatically, where `label` is the
shown name. The response SHALL NOT contain credentials.

#### Scenario: Two models offered

- **WHEN** `MODEL` is `openai:gpt-6.1-sol` and `CHAT_MODELS` is
  `openai:gpt-6.1-sol,anthropic:claude-opus-5-5`
- **THEN** the response has default `openai:gpt-6.1-sol` and the models `openai:gpt-6.1-sol`
  (`gpt-6.1-sol`) and `anthropic:claude-opus-5-5` (`claude-opus-5-5`) in that order

### Requirement: Non-chat requests use the default model

The training-summary endpoint and any other model request that does not belong to a chat thread
SHALL use `MODEL`, whatever model chat threads are using.

#### Scenario: Summary after switching chat

- **WHEN** the user's latest chat thread runs on `anthropic:claude-opus-5-5` and the daily
  summary is requested
- **THEN** the summary is generated with `MODEL`
