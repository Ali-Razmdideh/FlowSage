# FlowSage

**Predictive & Observed UX Intelligence Platform**

FlowSage merges two halves of UX analytics that today live in separate tools: predicting friction before launch, and measuring it after.

- **Predictive engine** — multimodal LLM persona agents walk through a product's UI (Figma exports, screenshots, or a live staging URL) and produce a structured friction report before a single real user touches the flow.
- **Observational engine** — real user event streams are modeled as a temporal graph in Neo4j; every screen, tap, and hesitation becomes a node or edge, surfacing where journeys stall, loop, or die.
- **Calibration loop** — FlowSage compares synthetic persona predictions against real user behavior, scores its own accuracy, and refines the personas over time, converging toward calibrated digital twins of the real user base.

Together these answer three questions no current tool answers in one place: where users *will* struggle, where they *are* struggling, and how good the platform is getting at predicting the difference.

## Features

**Predictive engine (synthetic users)**
- Configurable LLM personas (novice, power user, accessibility-constrained, low-patience mobile, non-native speaker)
- Traverses Figma files, screenshot sequences, or crawled staging environments
- Evaluates against Nielsen heuristics plus custom rubrics
- Friction reports with severity scores, screen-level annotations, suggested fixes
- Multimodal vision catches visual issues (contrast, tap-target size, misleading affordances), not just flow logic

**Observational engine (journey graph)**
- SDK/webhook ingestion of product event streams into a temporal user-journey graph
- Automatic funnel discovery, no manual funnel definitions
- Friction-node detection: abnormal drop-off, rage-loops, backtracking
- Cohort path comparison
- LLM-generated plain-language explanations of *why* a drop-off is likely failing, grounded in session context
- Churn prediction per segment with ranked re-engagement recommendations

**Calibration loop (the differentiator)**
- Prediction-vs-reality scoring: every pre-launch persona prediction matched against post-launch graph evidence
- Persona accuracy dashboards over time
- Miscalibrated personas auto-retrained on real behavioral data

**Workflow layer**
- Trend tracking and alerting on emerging friction
- Slack/Jira integration that auto-files annotated tickets
- Weekly digest reports
- Insights API for downstream tooling

## Tech Stack

- **Agentic orchestration:** LangGraph for persona simulation
- **Graph modeling:** Neo4j for temporal user-journey graphs
- **Vision/LLM:** multimodal models for reading UI screenshots and generating friction reports
- **API:** FastAPI

## Roadmap

| Phase | Timeline | Goal |
|---|---|---|
| 0 — Hobby / Soft launch | Months 1–3 | Two standalone scripts: screenshot-sequence persona walkthrough → markdown friction report, and event-log → Neo4j journey graph with funnel visualization. Open-sourced. |
| 1 — MVP | Months 4–7 | Single web app: Figma/screenshot upload → persona panel → friction report; simple event stream → funnel view with friction nodes and LLM explanations. Single-tenant, manual onboarding. |
| 2 — MLP | Months 8–12 | Prediction-vs-reality dashboard, cohort comparison, churn-risk scoring, trend alerts, Slack/Jira auto-ticketing, configurable/savable personas. |
| 3 — Beta | Months 13–16 | Multi-tenant architecture, workspace roles, SOC2-track security, 10–20 pilot companies (Malaysian startup ecosystem beachhead), case studies. |
| 4 — Final release / Monetization | Month 17+ | Tiered SaaS subscription, Figma plugin (self-serve persona audits), enterprise digital-twin engagements, freemium floor. |

## Why This Project

Each phase forces a new competency — multimodal agents, graph analytics, streaming pipelines, multi-tenant SaaS, billing, B2B sales — while building on existing experience, so progress never stalls on pure unknowns. It's a horizontal B2B SaaS product with zero overlap with unrelated core products, and a natural companion to a UX analytics background: domain intuition from the day job, a lab for the side project. Worst case, it's a strong AI-for-UX portfolio piece; best case, the calibration loop is a real company.
