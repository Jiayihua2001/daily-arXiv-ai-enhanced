---
title: Genetic Algorithms for Molecular Crystal Structure Prediction
slug: genetic-algorithm-csp
summary: >
  How evolutionary search has become the default candidate-generation engine
  for organic-molecular CSP — and where it's losing ground to generative ML.
tags: [csp, genetic-algorithm, polymorphism, computational-chemistry]
status: working-draft
last_updated: 2026-05-06
parent_topic: mcsp
authors: [Jade]
---

# Genetic Algorithms for Molecular CSP

> **In one sentence**: instead of sampling crystal candidates uniformly at random, evolve a *population* of candidates — the best ones interbreed (cross over crystal halves), mutate (jiggle space group, Z′, conformer), and over generations the population concentrates near low-energy minima.

## Background

Genetic algorithms (GAs) come from optimization, not crystallography — they were used for protein folding, scheduling, and circuit design long before CSP. The application to crystals is straightforward in principle: a *crystal* is a vector of (space-group, lattice parameters, molecular position+orientation+conformation), and any vector-valued thing that can be evaluated by a fitness function (lattice energy) is something a GA can search.

But molecular crystals add three twists:

1. **Symmetry constraints**. Random gene-mixing usually produces an unphysical structure (atom overlap, broken symmetry). Crossover operators must respect space-group symmetry.

2. **Local minima are dense**. Energy landscape has 10⁵–10⁷ local minima within ~10 kJ/mol — niching (preventing the population from collapsing onto one minimum) is essential.

3. **Fitness is expensive**. One DFT energy evaluation can cost tens of CPU-hours. So GAs for CSP are tightly intertwined with cheap surrogate energies (force fields, MLIPs).

The modern molecular-CSP GA (GAtor and successors) is roughly:

```
init: population of N≈100 random candidates
loop until converged:
  evaluate fitness (cheap energy → DFT)
  select parents (tournament, with niching)
  crossover (combine asymmetric units / lattice halves)
  mutate (jiggle Z′, swap space group, perturb conformer)
  niche & cull (no two structures within ε in similarity)
output: low-energy basins found
```

## Why GAs vs random search?

For a uniform search of crystal-structure space, you need to cover ~10⁶ candidates per molecule. Even at 10 ms / cheap-energy evaluation, that's 3 hours — manageable. But the *useful* candidates are tightly clustered in low-energy regions. GAs concentrate effort there.

| | Random search (Genarris 1/2, AIRSS) | Genetic algorithm (GAtor) |
|---|---|---|
| Coverage | Excellent at uniform sampling | Concentrates on low-energy basins |
| Compute | O(N) for N candidates | O(N × generations) — but smaller effective N |
| Diversity | Naturally high | Risk of collapse → needs niching |
| Best-of-class for | Fast initial sweep | Refining the bottom 1% of energy |

In practice modern pipelines (e.g. Genarris 3.0) do both: random search → cheap rank → GA-refine the survivors.

## Methods landscape

### The original USPEX-mol family (early 2010s)
Oganov & co. extended USPEX (originally for inorganic CSP) to molecular crystals. Demonstrated GA could find experimental forms but was slow to scale.

### GAtor (2018)
The Marom group's first-principles GA. Key choices:
- **Crossover**: cut a parent crystal along a plane in fractional coordinates; take half from each parent; reattach.
- **Niching**: structure descriptors (Steinhardt parameters, RDF) to detect duplicates.
- **Energy hierarchy**: start with cheap force field, escalate to DFT only on niches that survive.
- **Parallelism**: each generation evaluates ~100 candidates in parallel via FHI-aims; throughput is wall-clock, not compute, limited.

*wiki: [[entities/gator]], [[concepts/genetic-algorithm-csp]], [[sources/2026-05-02_gator]]*

### MAGUS (2020s)
A more recent GA framework with built-in transfer learning between species (lessons from CSP of compound A inform compound B if related).

### Hybrid: Genarris 3.0 (2025)
Not strictly a GA, but instructive contrast: random sampling + Rigid-Press geometric compaction + MLIP relax + clustering. Authors (also Marom group) explicitly position this as competing with their own earlier GA — random + good local relaxers can match GA quality on simple molecules at lower wall-clock cost.

*wiki: [[entities/genarris]], [[concepts/rigid-press-algorithm]], [[sources/2026-05-02_genarris-3]]*

## Background knowledge

To read GA-CSP papers you'll need:

- **Crystal descriptors**: Steinhardt order parameters (Q₄, Q₆), radial distribution functions, COMPACK / XPac similarity metrics. These are how a GA decides whether two candidates are "the same".
- **Niching strategies**: Standard methods are deterministic crowding (best-vs-most-similar), restricted tournament selection, fitness sharing.
- **Crossover and mutation operators specific to crystals**:
  - *Slice crossover* (most common): cut two parent unit cells along a plane, paste halves.
  - *Generalized convex hull*: maintain a Pareto front of energy vs. compositional descriptors.
  - *Soft-mutation*: perturb along low-frequency phonon modes.
- **Surrogate energies**: W99, GAFF for force fields. MACE-OFF, AIMNet2 for MLIPs.

## Core papers

### GA frameworks
1. **[Curtis, Wang, Marom, 2018 — GAtor](https://doi.org/10.1021/acs.jctc.7b01073)** — the canonical molecular-CSP GA. Read this and Genarris back-to-back. *wiki: [[entities/gator]]*
2. **[Lyakhov, Oganov, et al. — USPEX](https://doi.org/10.1016/j.cpc.2013.05.027)** — USPEX general algorithm; molecular extension followed.
3. **[Wang, Lv, Zhu, Ma — CALYPSO](https://doi.org/10.1016/j.cpc.2012.05.008)** — particle-swarm alternative; not GA but in the same evolutionary-search family.

### Niching and operators
4. **[Curtis et al., 2018 — Niching in GAtor](https://doi.org/10.1021/acs.jctc.7b01073)** — appendix discusses crystal-specific descriptors used for niching.

### Comparing GA vs random vs ML
5. **[Yang et al., 2025 — Genarris 3.0](https://doi.org/10.1021/acs.jctc.5c00226)** — the Marom group's own assessment that Rigid-Press + ML beats their earlier GA on cost-vs-coverage.

### Generative ML — the new competitor
6. **[Jiao et al., 2023 — DiffCSP](https://arxiv.org/abs/2309.04475)** — diffusion-based crystal generation; bypasses GA-style search entirely.

## Insights — where GAs are losing and where they're holding ground

- **Where they're losing**: simple, rigid molecules. Genarris 3.0 (random + Rigid-Press + MLIP) matches GAtor at lower wall-clock cost. For pharmaceutical screening at scale, the GA's overhead may not be worth it.

- **Where they're holding**: flexible molecules and multi-component crystals. The GA's notion of *niching by similarity* generalizes to "find diverse low-energy basins" in a way random search doesn't. When the search space has many distant low-energy minima (which is the *flexible-molecule* problem), GAs still win.

- **The hybrid future**: my read is that *pure GA* loses to *hybrid pipelines*. The most likely 2026–2028 pattern: random + MLIP-relax for breadth + GA only on the surviving basins for depth. Or: generative model proposes initial population + GA refines.

- **Reproducibility worry**: every GA paper reports its own niching/crossover settings; cross-paper comparison is hard. The community needs a standardized GA-CSP benchmark beyond CCDC blind tests, where the *protocol* is fixed and only the genetic operators vary.

## Open questions

1. Can crossover operators be *learned* (from CSD data) rather than hand-designed? Equivariant networks predicting "good" cuts.
2. How well do GA and generative methods compose? Does using a diffusion-model-sampled population as the GA initial state actually help, or does it just shift the niching problem?
3. Is there a fundamentally better fitness signal than lattice energy alone — e.g., predicted experimental observability (would this polymorph actually nucleate)?
4. Why does random + Rigid-Press *match* GAtor on simple molecules but presumably lose on flexible ones — is there a quantifiable test for when each wins?

## Where this fits in your work

You're inside the group that built GAtor. The interesting questions for your dissertation aren't "can GAs do CSP" (settled) but "when do they still win, and what's the best hybrid". Concretely: a benchmark study comparing GAtor vs Genarris 3.0 vs DiffCSP on the same target set, with cost-vs-coverage curves, would be a publishable contribution.
