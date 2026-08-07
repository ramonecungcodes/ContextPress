---
title: "Evals are the actual product"
date: 2026-06-22
tags: [craft]
description: >
  The teams that win obsess over how they measure quality. A case for treating
  evaluation as a first-class artifact.
---

Every team building with LLMs eventually hits the same wall: the model output
*feels* worse after a change, but nobody can say by how much, or why. The teams
that get past this wall all did the same thing first — they built evals they
trusted before they tried to improve anything.

## Vibes don't survive a second engineer

A single person can hold "is this good?" in their head for a while. Two people
can't. The moment a second engineer changes a prompt, you need a shared,
reproducible answer to "did this help or hurt?" That answer is an eval set.

An eval set is three things:

1. A fixed collection of **inputs** that represent real usage.
2. A **grader** — exact match, a rubric, or a model-as-judge — that scores output.
3. A **number** you can watch move.

## Start with the failures you already have

You don't need a thousand cases. You need the twenty that already went wrong.
Every support ticket and "the bot said something dumb" screenshot is a labeled
example waiting to happen.

```text
input:    "Cancel my subscription and refund the last charge."
expected: routes to billing; does NOT promise a refund it can't authorize
grader:   rubric(refusal_when_unauthorized=required, tone=helpful)
```

## Model-as-judge is fine if you evaluate the judge

The common objection is "you can't grade a model with a model." You can — as long
as you spot-check the judge against human labels and track its agreement rate. A
judge at 92% agreement is a better quality signal than a human who reviews ten
cases a week when they have time.

The uncomfortable takeaway: the eval harness outlives every model you'll swap
through it. Treat it like the product it is.
