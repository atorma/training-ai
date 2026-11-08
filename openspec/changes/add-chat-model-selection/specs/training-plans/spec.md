# Spec Delta

## ADDED Requirements

### Requirement: Extraction runs on the chat run's model

Extracting or re-extracting a plan from chat SHALL use the model of the chat run that asked for
it, and the saved plan SHALL record that model as its extraction model. A retried run SHALL use
the retry's model.

#### Scenario: Extract on the selected model

- **WHEN** the user, on model `anthropic:claude-opus-5-5`, attaches `block-3.pdf` and says "here's
  my new plan"
- **THEN** the extraction runs on `anthropic:claude-opus-5-5` and the saved block records
  `anthropic:claude-opus-5-5` as its extraction model

#### Scenario: Re-extract after switching

- **WHEN** a block was extracted on `openai:gpt-6.1-sol` and the user, now on
  `anthropic:claude-opus-5-5`, asks to redo the extraction and confirms the warning
- **THEN** the new content comes from `anthropic:claude-opus-5-5` and the block records it
