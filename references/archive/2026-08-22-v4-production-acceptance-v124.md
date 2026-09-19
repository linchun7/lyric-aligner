# Smart v1.2.4 production acceptance gate

Smart v1.2.4 is a hardening release for the bilateral BPM bounded-stream tier.

Acceptance requires all of the following:

- production-shaped zero-width unmatched spans are treated as unmapped;
- mapped review cues cannot enlarge their canonical span into adjacent rows;
- Latin/mixed bounded-stream repartition fails closed until token-aware layout rendering exists;
- existing mapped 1:1 BPM recovery remains unchanged;
- cue count, numbering, and timing remain immutable in Smart;
- private real-song acceptance must not regress any previously human-confirmed cue.

Real song names, cue numbers, timestamps, and private lyrics are not committed as public regressions.
