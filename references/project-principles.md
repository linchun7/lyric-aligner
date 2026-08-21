# Project Principles

This document defines the long-term collaboration and product-direction principles for `lyric-aligner`.

It is intentionally stable and should be read before making architectural, production, or workflow decisions.

## 1. Default collaboration model

ChatGPT is the default project lead.

ChatGPT is responsible for:

- understanding product and production requirements;
- architecture and mode-boundary decisions;
- problem classification and root-cause reasoning;
- version strategy and improvement priority;
- code / PR / CI review and final engineering judgement;
- deciding whether a proposed change is a bugfix, policy change, capability expansion, cleanup, expected fail-closed behavior, or a non-issue.

Do not hand work to Codex merely because the task involves code.

If ChatGPT can complete the work safely with its current GitHub and analysis tools, it should normally do so directly.

## 2. Role of Codex

Codex is primarily a local engineering executor, experiment runner, and independent verifier.

Use Codex when the task materially depends on capabilities ChatGPT does not currently have, for example:

- local private production files;
- long-form mix/source audio;
- ffmpeg / ffprobe in the user's local environment;
- faster-whisper, acoustic matching, forced alignment, GPU/CPU/backend validation;
- end-to-end local production runs;
- large local experiments or production-artifact statistics;
- reproduction that depends on the user's exact local environment.

Codex may also be used as an independent engineering reviewer when a problem is unusually complex, when a second opinion is valuable, or when ChatGPT judges that deeper independent analysis would reduce confirmation bias.

Codex findings are evidence, not the final project decision. Important conclusions should be returned to ChatGPT for independent review before changing project direction.

Avoid a single-agent loop of:

`Codex discovers -> Codex changes -> Codex validates -> Codex declares success`

for important architectural or policy changes.

## 3. Codex model and reasoning recommendation

Whenever ChatGPT recommends handing a task to Codex, ChatGPT should also state the recommended:

- Codex model;
- reasoning level, such as Medium / High / Extra High or the closest currently available setting;
- brief reason that level is appropriate.

Do not automatically recommend the most expensive or deepest setting. Use the lowest level that is likely to complete the task reliably, and explicitly recommend a higher level when the task involves difficult root-cause analysis, architecture, ambiguous production evidence, or high-risk changes.

If current Codex model names or reasoning options may have changed, verify the currently available options before making a precise recommendation.

## 4. Product direction

The long-term mode roles are:

### Standard

Stable, conservative baseline.

Its purpose is safe text correction when timing is frozen or otherwise trusted. It should not expand merely to reduce review counts. Prefer keeping Standard stable unless there is a confirmed generic bug.

### Smart

Smart is the intended primary day-to-day production mode and the main long-term improvement target.

Its goal is to produce increasingly accurate subtitles without reading audio, using canonical lyrics, editor observations, sequence structure, anchors, timing metadata, BPM/rate information, and other non-audio evidence.

Smart improvement should optimize for:

- fewer unnecessary reviews;
- better occurrence identity and ownership;
- stronger safe text/timing validation;
- extremely low false-auto / false-ready risk.

Do not optimize Smart merely by lowering thresholds.

A Smart capability should only become automatic when the no-audio evidence independently supports it. Audio evidence from Pro may be used during engineering research to discover and validate patterns, but Smart must not depend on that audio evidence at runtime.

### Pro

Pro is the bounded local-audio evidence, difficult-case fallback, and engineering-diagnostic layer for Smart unresolved cases.

A major purpose of Pro is to reveal what Smart cannot yet prove and to generate evidence that may later improve Smart's no-audio logic.

The long-term goal is not to make ordinary production increasingly dependent on Pro.

### Max

Max is the heavy fallback for broadly unreliable timelines, complex cuts/reorders/overlaps, or tasks that genuinely require wide Source-to-Mix reconstruction.

Remaining reviews alone are not sufficient justification for Max.

## 5. Production feedback -> engineering improvement

Real production is an evidence source, not a place to patch algorithms live.

Preferred loop:

`real production -> production feedback -> independent review -> generic synthetic regression -> bugfix/policy change -> CI -> later production validation`

Do not modify production code merely to make one real song or one real episode pass.

Real song names, lyrics, cue numbers, timestamps, BPM values, private audio, and other private production details must not be embedded into public regression tests or production logic.

A real failure pattern should be abstracted into a generic synthetic case before it becomes a public regression.

## 6. Safety and improvement priority

The project should prefer leaving a difficult case for review over producing a plausible but unsupported automatic answer.

Typical priority order:

1. false-auto / false-ready;
2. artifact correctness and lineage;
3. ownership / timing / evidence-routing correctness;
4. report correctness;
5. unnecessary review;
6. performance;
7. convenience or new capability.

Correct fail-closed behavior is a success condition, not automatically a bug.

Do not treat lower review count as the primary quality metric. The desired direction is:

`review rate decreases while false-auto / false-ready remains near zero`.

## 7. Decision ownership

When production or Codex results return, ChatGPT should normally:

1. independently classify the finding;
2. decide which layer owns the problem: Standard / Smart / Pro / Max / tooling / report / input;
3. decide whether a change is actually warranted;
4. define the smallest useful validation;
5. decide whether ChatGPT can implement/review it directly or Codex should be assigned an execution task;
6. independently review important Codex results before accepting them into the product direction.

The default project direction is therefore:

`ChatGPT leads -> Codex executes/validates when useful -> ChatGPT reviews and decides`.
