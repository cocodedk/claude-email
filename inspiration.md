https://www.youtube.com/watch?v=D7_ipDqhtwk

---

## What the talk is really saying (stripped down)

### 1) Don’t use agents by default

Use an agent only if:

* The task is **ambiguous** (you can’t predefine the steps)
* The task is **valuable enough** to justify cost
* The core capabilities already work (no hard blockers)
* Errors are **detectable and recoverable**

If you can write a decision tree → do that instead. It’s cheaper, faster, more predictable.

👉 Translation for your setup:
Routing + workflows should be your baseline. Agents are the fallback for messy tasks.

---

### 2) Keep the architecture minimal

An agent is just:

* **Environment** (what it can see / state)
* **Tools** (what it can do)
* **Prompt** (rules + goal)
* **Loop** (model decides → acts → observes → repeats)

Everything else is optimization.

👉 The mistake most people make:
They overbuild orchestration before they understand behavior.

---

### 3) Think from inside the context window

The model:

* Only sees what you give it
* Has no memory beyond that
* Acts with incomplete, delayed feedback

So failures often come from:

* Missing context
* Bad tool descriptions
* Poor feedback signals

👉 Practical takeaway:
Debug by inspecting **what the model saw**, not what you intended.

---

### 4) Cost, latency, and errors scale with autonomy

As you increase “agent-ness”:

* cost ↑
* latency ↑
* failure impact ↑

So you need:

* budget awareness
* guardrails
* scoped autonomy

---

## What this implies for your routing harness

You’re basically building:

> a system that decides: workflow vs small model vs big model vs agent

That’s exactly the right direction.

---

## A simple mental model for routing

Think in layers, not models:

### Layer 1 — deterministic / cheap

* regex / rules / simple parsing
* embeddings / retrieval
* small model (fast)

### Layer 2 — structured workflows

* predefined steps
* limited branching
* predictable cost

### Layer 3 — “smart” single-shot

* one strong model call
* no looping
* used when reasoning is needed but bounded

### Layer 4 — agents

* looping
* tool use
* exploration

---

## How to decide difficulty (practical heuristics)

Don’t overthink this. You can start with crude signals:

### 1. Input ambiguity

* clear instruction → low
* vague / open-ended → high

### 2. Required steps

* 1–2 → low
* unknown / dynamic → high

### 3. Need for tools

* none → low
* multiple / conditional → high

### 4. Verifiability

* easy to check → safer for agents
* hard to check → avoid agents

---

## Example routing logic (simple but effective)

```python
def route(task):
    if is_trivial(task):
        return small_model

    if is_structured(task):
        return workflow

    if is_complex_but_bounded(task):
        return strong_single_call

    return agent
```

Where:

* `is_trivial` → classification, formatting, extraction
* `is_structured` → known pipelines (e.g. “analyze log → summarize”)
* `is_complex_but_bounded` → reasoning but no exploration
* else → agent

---

## Add budget awareness (this is missing in most setups)

Each task should carry:

```python
{
  "max_tokens": ...,
  "max_cost": ...,
  "max_steps": ...
}
```

Then:

* downgrade model if budget is tight
* cut off agent loops early
* fallback to simpler strategy

---

## Guardrails you’ll want early

1. **Max loop count** (e.g. 5–10 steps)
2. **Tool call validation**
3. **Fallback path** if agent stalls
4. **Logging every step** (critical for debugging)

---

## Where most systems break

* Routing too late (everything goes to big model first)
* No fallback when agent fails
* Overly complex agent setup
* Poor tool descriptions
* No visibility into agent decisions

---

## If I were building your harness

I’d start like this:

1. **Classifier step (cheap model)**

   * label: trivial / structured / complex / agent

2. **Router**

   * maps label → execution path

3. **Execution layer**

   * workflows
   * single-call models
   * agent loop

4. **Budget + guardrails wrapper**

5. **Logging + replay**

   * so you can inspect failures

---

## What still feels AI-ish in the original talk

* “think like your agent” is vague until you debug real failures
* “keep it simple” hides how messy tool design actually is
* no concrete routing or cost control strategy

---

## Final version (tight summary)

Good agents are for messy, high-value tasks where you can’t predefine steps and can still verify results. Keep the design minimal: tools, prompt, loop. Most problems should still be solved with workflows or single model calls.

For your harness, the real win is routing early. Use cheap signals to decide whether a task needs a simple model, a workflow, or an agent. Add hard limits on cost and steps, and always keep a fallback path.

If you get that right, you’ll spend far less and your system will feel faster and more reliable.

