# High-Level Project Plan

## Problem Definition

### Background

Software engineering teams work in environments of high information density. Product decisions are discussed in Slack threads, architecture tradeoffs live in design documents, implementation details change across pull requests, and critical updates are shared in meetings that may or may not be documented. This creates a fragmented landscape of organizational information.

Unlike code, which is organized in version-controlled repositories, organizational knowledge has no unified structure. Engineers must reconstruct context by searching documentation, asking coworkers, and piecing together fragmented conversations.

This problem is particularly acute for mid-sized startups (20-150 engineers). They are not too large to have entrenched internal systems, yet small enough that formal systems of information flow are still maturing. As these organizations continue to scale, the gap between information generation and information coordination widens.

### Consequences

#### Invisible Knowledge Silos

Teams lose visibility into who knows what -> parallel efforts and misaligned execution

#### Decision Drift and Contradictions

Decisions may change informally. What was agreed upon in one meeting may not propagate to affected teams. Documentation often lags behind implementation -> rework, technical debt

#### Slower onboarding and context reconstruction

New engineers must manually reconstruct organizational context by searching Slack history, reading outdated documentation, and asking colleagues -> increases ramp-up time and more burden on senior engineers

#### Slower alignment, Slower execution

Changes in one service might silently impact others. Teams discover conflicts late in the implementation cycle -> slower iteration, delayed launches

### Statistics

Developer Productivity Loss in Tech Companies (Key Statistics).pdf

## Current Solution + Deficit

### Current solution

Mid-sized engineering teams rely on a combination of collaboration and documentation tools to organize information. People communicate through Slack, emails, and meetings. Technical designs and decisions are documented in Notion or Google Docs. Code changes and discussions are tracked in GitHub. Implementation progress is tracked in task management tools like Jira or Linear.

### Deficit

These tools don’t model organizational knowledge as a dynamic, interconnected system. Information is stored in various locations, but not mapped. Communication is reactive, not routed. Organizational memory is not versioned. Onboarding relies on static artifacts.

Current systems are tool-centric rather than intelligence-centric. They enable communication and storage, but they don’t actively coordinate, structure, or reason over organizational knowledge.

## Need Statement

We need a way to model organizational knowledge in a connected, dynamic system so that decisions, context, and dependencies propagate reliably across relevant stakeholders.

## Design Inputs

### Customers

Engineering teams in medium-sized startups (20-150 engineers)

### Functional requirements

- Ingest and extract structured knowledge from multimodal communication sources
- Construct and maintain a dynamic organizational knowledge graph and stakeholder map
- Version and track evolving decisions and dependencies over time
- Intelligently route information based on contextual relevance and impact
- Detect contradictions, outdated knowledge, and duplicate efforts
- Visualize reasoning, propagation paths, and stakeholder impact
- Generate personalized context views for individual engineers

### Constraints

- Support low-friction interaction, including voice
- Continuously updating
- Context-aware
- Scalable
- Handle teams of 20-150 engineers
- Handle multiple teams working on different services
- Transparent & explainable
- Non-intrusive/respect privacy

## Proposed Solution

“We are creating an AI-powered organizational intelligence layer for mid-sized engineering startups, a system that turns scattered conversations, documents, and code discussions into a living, connected knowledge graph. By tracking how decisions evolve and propagate, it reduces misalignment, accelerates onboarding, and enables teams to scale without losing shared understanding.”

A dynamic, knowledge graph tool where each individual and team has a “digital twin”. The graph models ingests organizational information from sources like Slack, email, and meetings. It models relationships between stakeholders. As information flows within an organization, it updates the shared organizational knowledge base and propagates information intelligently to relevant stakeholders.

### MVP version

- Listen to a dedicated Slack channel (your “graph updates / standup” channel).
- Parse each message into a structured “graph diff”:
  - add/update a constraint on a project, and/or
  - add/update a dependency between projects
- Commit the diff to the graph as a versioned update with what/when/who/why + source link.
- Run conflict checks on every commit and, if triggered, notify only the relevant stakeholders (the minimal anti-spam routing rule).
