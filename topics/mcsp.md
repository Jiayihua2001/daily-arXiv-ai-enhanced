---
title: Molecular Crystal Structure Prediction
slug: mcsp
summary: >
  Predicting all the ways a molecule can pack into a crystal, ranked by stability,
  before anyone has grown the crystal in a lab.
tags: [csp, polymorphism, computational-chemistry]
status: working-draft
last_updated: 2026-05-06
authors: [Jade]
---

# Molecular Crystal Structure Prediction (MCSP)

> **In one sentence**: given a molecule, enumerate all the plausible crystal structures it could form and rank them by lattice energy — so you can predict which polymorph(s) Nature will pick before you've grown a crystal.

## Background

A single organic molecule can crystallize in dozens of distinct arrangements (polymorphs). Each polymorph has different physical properties — solubility, density, color, melting point, mechanical hardness, conductivity. For pharmaceuticals, the difference between polymorphs is the difference between a marketable drug and a recall: ritonavir's 1998 form-shift recall is the textbook cautionary tale. For energetic materials, it's the difference between detonation sensitivity profiles. For organic semiconductors, charge-carrier mobility can change by orders of magnitude between forms.

Experimentally, you find polymorphs by trial-and-error crystallization — different solvents, temperatures, additives, sometimes years of screening. MCSP asks: can we do this *in silico*, given only the molecular structure?

The output of an MCSP run is a ranked list of candidate crystal structures, lattice energies attached, with the experimental form ideally appearing in the top few.

## Why it matters

| Domain | Stake |
|---|---|
| **Pharma** | Polymorph selection drives bioavailability, shelf life, and patentability. The FDA cares. |
| **Energetic materials** | Sensitivity and detonation properties depend on packing geometry. |
| **Organic semiconductors** | Charge-carrier mobility hinges on  π-stacking patterns. |
| **Cocrystals** | Engineering multi-component crystals for solubility/stability is ~50% of modern formulation chemistry. |
| **Carbon capture / MOFs** | Adsorption selectivity is structure-dependent. |

## The gap (why this is hard)

Three structural difficulties:

1. **Sub-kcal/mol energy differences**. The known polymorphs of a single molecule typically lie within 1–5 kJ/mol of each other. To rank them, your energy method must be both fast (millions of candidates) *and* accurate to ~0.5 kJ/mol — well below DFT's typical error bars without dispersion corrections.

2. **Combinatorial search space**. Candidates ≈ (space groups: 230, in practice 5 dominate molecular crystals: P2₁/c, P-1, P2₁2₁2₁, P2₁, C2/c) × (molecular conformations) × (Z, the number of independent molecules per unit cell, often 1–4) × (packing geometry within space-group constraints). Naïvely, ~10⁵–10⁷ candidates per molecule.

3. **Dispersion is structural, not just numerical**. Plain GGA functionals (PBE, BLYP) miss van der Waals attractions almost entirely. Without dispersion (TS, MBD, vdW-DF), polymorph rankings invert. So you need *both* a working dispersion model *and* one fast enough to run on 10⁴ candidates.

## Methods landscape (how the field actually works today)

Modern MCSP runs as a **funnel** — coarse-and-cheap → fine-and-expensive — usually with three stages:

```
   ~10⁵–10⁶ candidates
      │
      ▼  cheap geometric / force-field rank
   ~10³–10⁴ survivors
      │
      ▼  ML potential (MACE-OFF, AIMNet2, UMA) relax + score
   ~10²    survivors
      │
      ▼  dispersion-corrected DFT (FHI-aims + MBD/TS) re-rank
   final ranking
```

Within that funnel, the **candidate-generation** step (the very first arrow) splits the field into competing camps:

| Approach | Examples | Idea |
|---|---|---|
| **Random structure search** | CSPy, [Genarris](#core-papers), AIRSS | Sample uniformly from space-group + Z′ space; rely on cheap relaxation to find minima |
| **Genetic algorithms** | [GAtor](#core-papers), USPEX-mol, MAGUS | Evolve a population: crossover crystals as parents, niche by similarity |
| **Generative ML** (newer) | [DiffCSP](https://arxiv.org/abs/2309.04475), [MatterGen](https://arxiv.org/abs/2312.03687), CDVAE | Train a diffusion / flow model on Cambridge Structural Database (CSD); sample from learned distribution |
| **Hybrid** | Genarris 3.0 (2025) | Random sampling + Rigid-Press hard-sphere compaction + MLIP relax + clustering down-selection |

For *re-ranking*, the choices are similar:
- **Force fields** (W99, GAFF) — fast but iffy on relative energies
- **MLIPs** trained on chemistry: MACE-OFF, AIMNet2, ANI — order-of-magnitude faster than DFT, accuracy depends on whether the chemistry was in training data
- **Foundation MLIPs** (UMA, GNoME, ORB, MACE-MP-0) — broad chemistry but mostly inorganic-trained; reliability on organics is open
- **DFT + dispersion** — gold standard, slow

The **CCDC blind tests** (organized by the Cambridge Crystallographic Data Centre) are the field's benchmark. Latest: 7th edition (2018–2024). Targets are 4–7 molecules per round; experimental polymorphs hidden until submission deadline. A method "wins" if the experimental form is in its top 1, top 5, or top 100.

## Background knowledge to read papers in this field

If you're new to the area, you'll keep tripping over the same handful of concepts. The fastest payoff:

- **Crystallography**: Bravais lattices, the 230 space groups (memorize the 5 most-common molecular ones above), Z and Z′ (Z = molecules per cell; Z′ = independent molecules in the asymmetric unit), and the Cambridge Structural Database as a data source.
- **DFT functionals + dispersion**: PBE, B86bPBE; dispersion corrections TS-vdW, MBD, vdW-DF, vdW-DF2. Most MCSP groups use FHI-aims or VASP with PBE+TS or PBE+MBD.
- **Lattice energy**: $E_\text{lat} = E_\text{electronic} + E_\text{dispersion} + E_\text{ZPE} + E_\text{vib}(T)$. The order of polymorphs can flip when you turn finite-T corrections on.
- **Quasi-harmonic approximation (QHA)**: cheap finite-T add-on; matters when polymorphs differ in vibrational density of states (e.g. aspirin Form I vs II).
- **MLIPs (machine-learned interatomic potentials)**: SchNet, PaiNN, NequIP, Allegro, MACE — equivariant graph NNs trained on DFT data. The 2024–2025 generation (MACE-MP-0, UMA, OMat24) aims to be "foundation potentials" usable across the periodic table.
- **The CSD vs the PDB analogy**: CSD ≈ AlphaFold's training data for *organic crystals*. ~1.3M structures.

## Core papers

A short reading list to get oriented. I've put internal markers (`wiki:`) where Jade's local wiki has a page — open them in Obsidian for the full notes.

### Foundational
1. **[Day, 2011 — "Current approaches to predicting molecular organic crystal structures"](https://doi.org/10.1080/0889311X.2011.575182)** — review; the *mental model* for the field. Read this first.
2. **[Reilly et al., 2016 — Sixth CCDC blind test](https://scripts.iucr.org/cgi-bin/paper?ce5077)** — the field's benchmark snapshot circa 2016.

### Methods
3. **[Curtis, Wang, Marom, 2018 — GAtor](https://doi.org/10.1021/acs.jctc.7b01073)** — genetic-algorithm framework; first-principles. *wiki: [[entities/gator]], [[sources/2026-05-02_gator]]*
4. **[Yang et al., 2025 — Genarris 3.0](https://doi.org/10.1021/acs.jctc.5c00226)** — Rigid-Press hard-sphere compaction + MLIP-driven workflow. The most recent fully-described MCSP pipeline; this is what you'll fork. *wiki: [[entities/genarris]], [[sources/2026-05-02_genarris-3]]*
5. **[Hoja et al., 2019 — Many-body dispersion for molecular crystals](https://doi.org/10.1126/sciadv.aau3338)** — why MBD over TS-vdW.
6. **[Whittleton et al., 2017 — On the difficulty of MCSP for flexible molecules](https://doi.org/10.1021/acs.cgd.7b01221)** — the conformational-flexibility wall.

### ML for MCSP
7. **[Batatia et al., 2023 — MACE-OFF23](https://arxiv.org/abs/2312.15211)** — transferable organic-molecule MLIP; current default in Genarris 3.0. *wiki: [[entities/mace-off]]*
8. **[Anstine et al., 2023 — AIMNet2](https://doi.org/10.1039/D3CP04419J)** — neural-network MLIP; system-specific fallback. *wiki: [[entities/aimnet2]]*
9. **[Jiao et al., 2023 — DiffCSP](https://arxiv.org/abs/2309.04475)** — diffusion-based crystal generation; mostly inorganic but the architecture transfers.

### Hard cases
10. **Energetic-material polymorphs** — the chemistry MACE-OFF was *not* trained on. *wiki: [[concepts/energetic-materials]]*

## Insights — what makes this hard *now* (and where the field is going)

- **The methodological wall has moved twice**. From 2000–2015 the gap was DFT cost. From 2015–2020, dispersion corrections (TS → MBD) closed the accuracy gap. From 2020 on, MLIPs are closing the cost gap. Each transition lasts ~5 years.

- **Foundation MLIPs are the open question**. UMA, OMat24, GNoME are trained mostly on inorganic crystals. Whether they generalize to molecular crystals — especially energetic materials, multi-component cocrystals, flexible molecules — is the *interesting* uncertainty in 2025–2026. MACE-OFF is the existence proof that you can train an organic-specific foundation potential, but its blind spots are real.

- **Generative methods haven't won a blind test yet**. DiffCSP, MatterGen, FlowMM all show good *unconditional sample quality* on CSD-like data, but turning that into a *ranked* list that beats GAtor is unsolved. Watch for the 8th CCDC blind test (likely 2026–2028) for whether this changes.

- **Multi-component crystals are the next frontier**. Cocrystals, salts, hydrates — basically the entire pharmaceutical formulation pipeline — are still mostly out of reach. The molecular degree-of-freedom explosion isn't the only problem; lattice-energy zero references become tricky.

- **Speed is the unlock for industrial adoption**. A pipeline that takes 10⁵ CPU-hours per molecule won't be used to screen pharma libraries. A pipeline that runs in 24 GPU-hours might be. MLIPs + Rigid-Press + smart down-selection (Genarris 3.0's contribution) suggests this may finally be achievable in 2026.

## Open questions

1. Can foundation MLIPs (UMA-class) be retrained / fine-tuned to match MACE-OFF accuracy on organics, while keeping breadth across the periodic table?
2. Will generative models reach top-5 in the 8th CCDC blind test, or will GA-based pipelines still dominate?
3. Is there a *unified* CSP framework for molecular + multi-component (cocrystals/hydrates) crystals?
4. How well do today's pipelines handle conformational flexibility (e.g. ROY, axitinib) at scale?
5. Can we fold finite-T (QHA, anharmonic) into the *initial* ranking step instead of as a final correction?

## Where this fits in your work

Jade's group (Marom, CMU) builds and benchmarks the GAtor / Genarris / FHI-aims stack. Your daily reading should be tilted toward (a) any new methodology paper from Day, Price, Neumann, Hofmann, Tkatchenko, Reilly, Beran groups; (b) MLIP papers that explicitly evaluate on organic crystals or energetic materials; (c) generative CSP papers benchmarked against the CCDC blind tests.
