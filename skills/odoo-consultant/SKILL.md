---
name: odoo-consultant
description: Run an Odoo implementation like an experienced consultant -- phases, roles, the SPoC, standard vs custom decisions with a simple ROI rule, scope control and change management. Use when the user plans or runs an Odoo implementation, weighs a custom wish against standard Odoo, writes a gap analysis or user story, prepares a go-live, or deals with resistance from users.
triggers: [consultant, implementation, standaard, standard odoo, implementatie, go-live, golive, spoc, key-user, keyuser, gap, maatwerk, customisation, customization, user story, change management, roi, scope, hypercare, adkar]
---

# Odoo consultant

Follows Odoo's own implementation methodology, with field practice on top. For
*which app fits a process*, use the `odoo-advisor` skill; this one is about
running the project.

A project succeeds when it goes live **on time and on budget**, not when every
wish is granted. Over half of proprietary ERP implementations fail; Odoo claims
>95% success over five years by delivering on time and on budget and keeping
custom work small.

**Standard wins, unless the gap is real and the numbers say otherwise.** Roughly
70-85% of requirements are covered by standard Odoo plus configuration. Custom
work is not the enemy, unmanaged custom work is: complexity grows with the square
of the number of customisations, not linearly.

## 1. Principles

- **On time and on budget is the only real success criterion.** Custom
  development, satisfaction during the project and early upsell come second.
  Upsell only after go-live: selling more to an existing customer is far easier,
  and every upsell before go-live erodes trust.
- **Customer satisfaction is not a useful KPI.** It moves with every phase (a dip
  just before go-live, a rise in production) and every person wants something
  else: a key-user wants features, the CEO wants time and budget. Better a
  customer who is briefly unhappy because you pushed back than a missed deadline.
  Use satisfaction only to gauge key-user motivation.
- **Manage expectations: promise less, deliver more.** No tight deadlines, no
  promises of complex features, never say the change will be easy, don't say yes
  to everything. Raise a doubt about the plan the moment you have it.
  > A CEO asked before signing: "promise me this will go smoothly." Odoo's
  > founder answered: "No. It will be hard and we'll hit many issues. But your
  > company will be better, and I need you to back the project when your teams
  > complain." A year of delay later, the CEO still backed it.
- **Keep it simple, decide for the customer.** Propose the best solution, show the
  alternative only if the customer is not satisfied. Force decisions, also under
  uncertainty: no "let's ask the key-users" or "I need to check with a manager".
  Don't let the customer label features as necessary or optional, or everything
  becomes necessary. Common sense beats any rule.

## 2. Roles

**Implementer side.** One **Project Leader** wears three hats: project manager
(deliver on time, onboard the SPoC), business analyst and product expert (decide
how needs are met, challenge requirements, configure, migrate data, write specs)
and digital advisor (spot the core challenges and the digital opportunities). A
developer joins only for real custom work and talks to the Project Leader, not the
customer, so every request gets challenged. An app expert outside the project
peer-reviews at critical moments. Few hats, few hand-offs.

**Customer side: the SPoC (single point of contact).** The "super key-user" who
shares responsibility for success: collects and judges requirements, trains
end-users, becomes the internal Odoo expert and first-line support. Two hard
requirements: the SPoC is **available** and has **decision authority**. Without
them every decision still goes past the CEO and the project stalls. Larger
projects add a steering committee, key-users (domain experts who test and
validate) and sponsors.

## 3. Phases

| Phase | Time | Goal |
|---|---|---|
| ROI analysis | 10% | Cost/benefit, phasing and budget. For most SMEs part of the kick-off |
| Kick-off | 5% | Align stakeholders on the method, standard training |
| Implementation | 80% | Short cycles: analysis, configuration, validation, key-user training |
| Go-live | 5% (10-15% on large projects) | End-user training, bug fixes |
| Second deployment | variable | Broaden scope, deferred features |

- **ROI analysis.** Meet stakeholders (goals, motives, risks), run "show me how
  you work" workshops per department on the *as-is* and its pain points (not the
  to-be), peer-review, then show a proof of concept of a few flows. Split
  necessary custom work into *needed before production* and *later phase*.
- **Implementation.** Keep a steady pace and deliver something every week. The
  Project Leader configures (Studio included). Import master data, not history:
  history eats budget and adds risk. Ask how often and why they would look it
  up; keep it in the old system or an export, and if at all, import it after
  go-live. Don't delay production for data quality; clean up in Odoo afterwards.
  Key-users click themselves; the SPoC does the final tests and gives the go.
- **Go-live.** Surprises come from an untested database or untrained users. A
  training is not a conference: key-users run the flows themselves. Don't
  postpone the date (motivation drops, new change requests, the import has to be
  redone). Be on site the first days, fix fast, and check after a few days that
  they really work in Odoo and not back in the old system.
- **Second deployment.** A month after go-live, review deferred development:
  typically about half is no longer needed.
- **Progress report and Digital Opportunities Matrix.** After go-live, a separate
  meeting with top management: how do we help their people do more in less time?
  Score opportunities on impact x ease: *quick wins* first, then *game changers*
  or *fine tuning*, avoid low-impact complex work. Bring the top 3, not 10.
  Collect them from day one.

## 4. Working together: one project per customer

Run each implementation as one project in the implementer's own Odoo (Project
app), with portal access for the SPoC. If it isn't there, it doesn't exist.

- **One mail alias per customer on that project.** Every mail becomes a task;
  mail that lands in a private inbox gets forwarded to the alias.
- **Send mail from the task** (chatter, *Send message*), not from a mail client.
  Question, advice and decision sit in one place and a colleague can take over.
- **Meeting notes** go as a note on the task or project, not as an attachment.

### Three kinds of requests, three routes

| Kind | Example | Route | Who handles it |
|---|---|---|---|
| Odoo question | "How do I make a credit note?" | 1. AI with read-only Odoo access and the customer handbook. 2. The SPoC. 3. Only then the alias | SPoC; implementer is second line |
| Training | New colleague, new flow | Implementer trains the SPoC and key-users, the SPoC trains the rest | SPoC |
| Configuration or wish | "An extra field on the quote" | SPoC mails the alias -> task -> user story -> standard or custom (section 5) | Implementer, after the SPoC's go |

An Odoo question that comes back three times is a training gap or a mistake in
the handbook. Fix it there, not by mail.

### The SPoC as gatekeeper and trainer

The implementer advises, the SPoC decides. Keep the number of stakeholders small
and skip intermediaries who don't decide.

- **Gatekeeper.** Only the SPoC brings a wish to the implementer, and only the
  SPoC labels it: **go-live**, **after go-live** or **no**, in a fixed weekly
  30-minute meeting. Key-users take their wishes to the SPoC.
- **Promise nothing at the table.** Standard answer: "I'll put it on the list, the
  SPoC decides." A wish labelled go-live shows how many hours it costs and which
  task moves because of it.
- **Trainer and first line.** Nobody trains better than a colleague who knows the
  processes. Make the SPoC and key-users capable early; they sign off the
  scenarios before go-live.

## 5. Choosing: standard Odoo or custom

### User stories

Every wish comes in as a user story: *As [role] I want [feature or fixed bug] so
that [goal].* The goal always falls under one of four: **cost saving,
efficiency, more revenue, job satisfaction**. If it fits none, it is a habit,
not a wish.

### The five steps (per process, always in this order)

1. **Now** -- the current process in at most 10 steps: who, what, which system,
   how often. Watch, don't ask. A rough understanding is enough.
2. **Odoo standard** -- how the standard process for this type of company
   (manufacturing, trading, retail, services) runs in Odoo and which
   configuration belongs to it, checked against the docs and shown on a staging
   database. Discuss it with the customer.
3. **Gap** -- hold the standard against the user stories. One label per point:
   - *habit*: different from now, same result -> change the process, no discussion.
   - *real*: the standard cannot deliver the result (law, customer requirement, money).
4. **Options** -- A: adapt the process to standard. B: custom. Option B only
   exists for *real* gaps.
5. **Decision** -- the SPoC chooses: now, later or no. Then write down how the
   current process changes to fit the standard (or the custom solution).

**Ask why three times** on every custom wish. Usually: "because that's how we do
it", then "because the old system worked that way", then "because it had to". If
the third answer is a limitation of the old system that no longer exists, the gap
is a *habit* and option B goes away.

### Four questions to challenge a wish

1. **Is it really needed?** Did they have it in the old software? Can it be done
   by hand in Odoo? Go live without it and decide after a few months; priorities
   change in production.
2. **Is the cost worth it?** Weigh the benefit against the running cost: one-off
   development cost x2-3 for maintenance and upgrades over five years. Custom
   work is technical debt of ~25% of the build cost per year (~17% maintenance,
   ~8% upgrades).
3. **Is the gain big enough?** 10 projects a month x 10 minutes of manual work is
   under 2 hours a month, not worth 10 days of development.
4. **Can it be done differently?** A sync service instead of a connector, a policy
   instead of an approval step.

Still not worth it? Explain why, plan it after go-live, or escalate. A community
module also costs testing, maintenance and upgrades.

### ROI rule (three years, no spreadsheet needed)

```
Cost custom (3 yrs)    = build hours x rate x 1.75     # build + 25%/yr maintenance and upgrades
Cost adapting          = training hours + adjustment loss (hours x people)
Benefit custom (3 yrs) = (minutes saved per time x times per year / 60) x customer hourly rate x 3
                         + avoided errors in money, only if demonstrable
```

Custom only if **the gap is real** and **benefit > 2 x (cost custom - cost
adapting)**. The factor 2 covers estimates always being optimistic. When in
doubt: standard, and the custom wish goes on the backlog until after go-live
(half of them die there). Deliberately not used: weighted scoring models, NPV,
sensitivity analyses.

### Where it is recorded

- **The file**: one subtask per decision under a parent **Decisions** task in the
  customer project, described with the template below. Advice as an internal
  note, the customer's decision in the chatter. If the choice waits on the
  customer: mark the task blocked.
- **The outcome**, one line, in exactly one place: the customer's decision log
  (standard chosen), the development tracker linked from the task (custom
  chosen), or the custom-work backlog (later / no).

### When the SPoC goes against the advice

Respect the choice, but keep advice and options on the task, write down the
consequences (upgrade risk, maintenance load, higher three-year cost) and have the
SPoC confirm the deviation in the chatter. Responsibility then visibly sits with
the customer.

### When scope grows

- More custom work or hours: update planning, staffing (who runs the hours, what
  moves) and budget before the work starts, and put it to the SPoC explicitly.
- Agree a **custom-work budget** upfront, typically 15-25% of implementation
  cost. Every custom wish is deducted from it; when it's gone, re-budgeting is an
  explicit decision.

## 6. Change management

Reserve **15-20% of the implementation budget** for training and change
management together. Small teams that do this well use four habits rather than a
framework:

1. **Fixed scope with one gate** -- the SPoC as gatekeeper (section 4).
2. **A weekly meeting with the key-users** -- one fixed moment for problems and decisions.
3. **Key-users click early and sign off before go-live** -- they say yes to go-live, not the implementer.
4. **Two to three weeks of hypercare after go-live** -- the first three months are only about stability.

Phased go-live is the norm (over half of projects); about a fifth go big bang.

### Resistance

Don't sideline the unconvinced. Listen, explain the why and the how, sell the
solution through training. Change always feels like cost and risk; don't claim it
is risk-free. Show the benefit and people accept the risk. The most critical
person is often the best prepared at go-live. Get key-users on board before you
start, and stay out of internal politics: fix the issue, never mind whose fault
it was.

SPoC profiles: *do it now* (too fast; double-check, train in tandem), *do it
right* (resists; challenge on value, not "it's standard"), *do it harmoniously*
(wants control; extra training), *do it together* (endless ideas; make roles
clear: SPoC = what and why, implementer = how).

### ADKAR per person

When someone is stuck, use ADKAR (Prosci) to find where:

- **Awareness** -- do they understand why? Be concrete: time lost to re-entry,
  recurring stock errors, reports that don't add up. Nothing vague about
  "digital transformation".
- **Desire** -- do they want to take part? The hardest step: understanding is not enough.
- **Knowledge** -- do they know how to do it differently?
- **Ability** -- can they actually do it?
- **Reinforcement** -- does it stick?

### Other frameworks, briefly

- **Kotter, 8 steps** -- for the organisation: urgency, guiding coalition,
  one-sentence vision, communicate, remove obstacles, quick wins, keep going,
  anchor. Works as a playbook towards a go-live.
- **BCG's DICE** -- the only one you can calculate. `D + 2·I + 2·C1 + C2 + E`,
  each 1 (good) to 4 (bad): **D**uration between formal reviews, **I**ntegrity of
  the team, **C**ommitment of the top (C1) and of the people doing the work (C2),
  **E**xtra effort. 7-14 is the win zone, above 17 the woe zone. Score it now and
  with the plan; the gap shows which levers matter.
- **McKinsey's influence model** -- role modelling by leadership, understanding
  the why, formal reinforcement (old system read-only), skills. Their "70% of
  transformations fail" figure has no traceable source.
- **Bain** -- steer on whether people actually use it, not on whether the system
  was delivered.
- **Scrum is not the frame**: an ERP migration has fixed phases and one hard date.
  Within the configuration phase, work in weekly cycles.

## Task template

Advice on top, options from the user story below, whether it pays off at the bottom.

```
Process: <name>                        Customer owner: <name>
Advice:   <A|B, one sentence why>
Story:    As <role> I want <feature> so that <goal>  [cost|efficiency|revenue|job satisfaction]
1. Now:       <max 10 steps, frequency per year>
2. Standard:  <how Odoo does it + link to docs / staging>
3. Gap:       <point> -- habit | real
4. Options:   A adapt: <cost>   B custom: <build hours, 3-yr cost>
              per option: pros and cons
   ROI:       benefit 3 yrs <amount> vs 2 x (<B> - <A>) = <amount>  -> pays off | doesn't
5. Decision:  <now | later | no>, <name>, <date>
```

## With the Odoo MCP

- **Step 2 (standard)**: check what the customer's database already has before
  calling something a gap -- `list_models`, `search_records` on
  `ir.module.module` (`state = installed`), and `search_records` with
  `fields=["__all__"]` on a sample record to see the available fields.
- **Recording a decision**: `create_record` on `project.task` under the
  Decisions parent (`parent_id`), the template above as `description`. Then
  `post_message` with an internal note for the advice and a message for the
  decision (see the `communications` skill).
- Never change the customer's configuration from a wish the SPoC has not labelled.

## Sources

- Odoo, *Implementation Methodology* (2024). Toolbox: [ROI kick-off](https://www.odoo.com/r/roi_kickoff), [key-user interview](https://www.odoo.com/r/roi_key_user_intw), [ROI analysis tool](https://www.odoo.com/r/roi_analysis), [gap closing](https://www.odoo.com/r/gap_closing), [progress report](https://www.odoo.com/r/pu9), [change management](https://www.odoo.com/r/change_management), [blog](https://www.odoo.com/r/blog_implementation)
- [Change Management: the Odoo Implementation Methodology](https://www.youtube.com/watch?v=XdNbVGItHSg) and [Implementation Methodology & Q&A](https://www.youtube.com/watch?v=TTEA3blz-uI)
- [What is the ADKAR Model? (Prosci)](https://www.youtube.com/watch?v=sqcZ0ytGxp4) and [Kotter's 8 Steps](https://www.youtube.com/watch?v=SGYmPMcgDPg)
- [DICE framework (Wikipedia)](https://en.wikipedia.org/wiki/DICE_framework) and [The "Hard" Side of Change](https://flevy.com/blog/the-hard-side-of-change/)
- [Portcities: 6-Step Odoo Implementation for SMEs](https://portcities.net/services/odoo-implementation-smes), [Serpent CS: checklist before go-live](https://www.serpentcs.com/blog/erp-implementation-467/odoo-implementation-checklist-key-points-before-go-live-687), [QAD: 12 lessons learned](https://www.qad.com/blog/2025/05/erp-implementation-12-valuable-lessons-learned)
