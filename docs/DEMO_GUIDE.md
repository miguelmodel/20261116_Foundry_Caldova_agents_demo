# Caldova Control Tower — presenter guide

A short, step-by-step script for presenting the demo. It runs **12–14 minutes**, plus Q&A.

> **Story in one sentence:** a shipment for Caldova's spring launch is late, and two Microsoft Foundry agents work together to find the best recovery plan. The *Coordinator* (a prompt agent) delegates the number-crunching to the *Logistics Analyst* (a hosted LangGraph agent) over A2A, then checks company policy. Every step is visible live and traced end to end.

---

## 1. Before the demo

**The day before (or at least 3–4 hours ahead):** run all three scenarios once. Evaluation scores reach the **Monitor** tab in batches, up to a few hours after a run, so this guarantees step 3.6 has charts to show.

**About 20 minutes ahead:**

| ✅ | Step | How |
|---|---|---|
| ☐ | **Sign in to Azure** | `az login`, with the same account and subscription you deployed with. If `.env` sets `AZURE_CONFIG_DIR`, sign in to that profile: `$env:AZURE_CONFIG_DIR="$env:LOCALAPPDATA\caldova-demo-azure"; az login`. |
| ☐ | **Start the app** | From the repo folder: `.\start.ps1`. The browser opens **http://127.0.0.1:8000**. If the app is already running, just open that address. |
| ☐ | **Check the badge** | The top-right badge says **Agents connected**. If it says otherwise, click it to re-check (see [Troubleshooting](#6-troubleshooting)). |
| ☐ | **Do a warm-up run** | Run **Supplier delay** once. The first run after idle time is slower, while the hosted agent starts its container. |
| ☐ | **Copy the warm-up trace ID** | Click the copy icon next to **TRACE ID** at the bottom of the page. Paste it somewhere handy; you'll use it in step 3.5. Traces take 2–5 minutes to appear in the portal. |
| ☐ | **Refresh the quality scores** *(optional, ≈5 min)* | The Coordinator is scored automatically (see "The day before" above). For the Analyst, start an evaluation in the portal (**Build → Evaluations → Create**, agent **caldova-logistics-analyst**, dataset **caldova-analyst-scenarios**). It takes a few minutes, so do it ahead of time. See step 3.6. |
| ☐ | **Open the browser tabs** | Tab 1: the Control Tower app.<br>Tab 2: `docs/architecture.html` (double-click the file).<br>Tab 3: the Foundry portal, via the app's **Foundry** link (top right). |
| ☐ | **Tidy the screen** | Close unrelated tabs and notifications. Zoom the browser to 90% if the screen is small. |

---

## 2. Set the scene (≈1 min) — *Tab 2: architecture diagram*

**Say:**
> "Caldova is a fictitious retailer. Two weeks before its spring launch, a key shipment is late. Today you'll see two AI agents in **Microsoft Foundry** handle that disruption together, and you'll see exactly what each one does."

**Show** on the diagram, left to right:
1. **The laptop**: the app runs locally. A small Python backend relays everything the agents do to the browser.
2. **Caldova Coordinator**: a Foundry **prompt agent** on **GPT-5.5**. Its tools are **A2A** and **File Search** over the company policies.
3. **Caldova Logistics Analyst**: a Foundry **hosted agent** built with **LangGraph**, running as a container on **GPT-5.4-mini** (or Claude Opus 5.5, if your subscription can deploy Marketplace models and you deployed without `-NoClaude`). Its tools are ERP lookups and **Web Search** from a Foundry Toolbox (MCP).
4. **A2A**: the bold blue-to-purple arrow between the two agents. The agents talk to each other directly, inside Foundry.
5. **Application Insights**: every step lands in one end-to-end trace.

**Key message:** *"Two agents, two different models, two different ways of building agents, all working together in Foundry."*

---

## 3. Live demo

### 3.1 Run the main scenario (≈3 min) — *Tab 1: the app*

**Do:**
1. Check that **ORCHESTRATION MODE** is set to **A2A native** (the default).
2. Click the **Supplier delay** card (SH-2041 · Atlas Textiles). The prompt fills in automatically.
3. Click **Find a way forward**.

**Narrate as things appear.** The whole run takes about 40–60 seconds.

| What you see | What to say |
|---|---|
| Coordinator card lights up, then **"Delegating to Logistics Analyst via A2A"** in *The live trail* | "The Coordinator doesn't do the math itself. It hands the analysis to a specialist agent over **A2A**, the agent-to-agent protocol." |
| The **A2A** ring between the two agent cards lights up, labelled **via A2A** | "This call happens *inside Foundry*, from one agent to the other. My laptop isn't relaying it." |
| **"Analyst report ready"**; the Analyst card shows *Complete* | "The Analyst checked shipments, inventory and demand, and compared recovery options. It's built with LangGraph and runs as a hosted container." |
| **"Searching Caldova policies (File Search)"** | "Now the Coordinator checks the company rulebook: approval limits, the supplier contract, the disruption playbook." |
| The recommendation streams into **A clear next move.** | "And here's a decision-ready answer." |

### 3.2 Walk through the recommendation (≈1 min)

Scroll to **A clear next move.** and point out the sections. Exact wording varies from run to run; typical content:

- **Recommended action:** *reallocate stock to the flagship stores + air-freight part of the shipment*, about **EUR 28,000**. Flagship stockouts drop to zero.
- **Severity:** **S1 Critical** under the disruption playbook (launch within 14 days and a flagship stockout forecast).
- **Policy & approvals:** the plan falls in the **EUR 25k–100k** band, so the **Head of Supply Chain** must approve it, citing the policy.
- **Supplier claim:** a late-delivery penalty of about **EUR 16,740** can be claimed from Atlas, citing the contract.
- **Next steps:** owner roles and timing.

**Say:** *"The numbers come from the Analyst's ERP tools; the rules come from File Search over our policies. The agent recommends; a human approves."*

### 3.3 Show the hosted agent working live (≈2 min)

**Do:**
1. Switch **ORCHESTRATION MODE** to **Backend orchestrated**.
2. Click **Viral demand spike** (CV-SAT-0117 · Satin Midi Skirt).
3. Click **Find a way forward**.

**Say:**
> "A2A returns the Analyst's answer in one piece. In this mode my local app calls the Analyst directly, so we can watch the LangGraph agent's tools run live."

**Point at *The live trail*:** the `ERP: get_inventory_position`, `ERP: get_demand_forecast` and `ERP: evaluate_recovery_options` entries, and possibly `Searching the web`. Open **Inspect details** on one entry to show the real tool input and output.

### 3.4 Transparency in the app (≈30 s)

At the bottom of the page:
- **TRACE ID**, with a copy button.
- **Duration and tokens** for the run.
- **Raw events**: the actual event stream from Foundry. Open it briefly: *"Nothing is simulated; these are the real events."*

### 3.5 Observability in the Foundry portal (≈2–3 min) — *Tab 3*

> Use the **warm-up trace ID** from section 1. Traces from the run you just did may not have arrived yet.

**Do:**
1. In the Foundry portal, open **Agents → caldova-coordinator → Traces**.
2. Paste the trace ID into the search box and open the trace.
3. Expand the waterfall and point out:
   - `invoke_agent caldova-coordinator:<version>`: the Coordinator's turn.
   - `execute_tool remote_a2a_caldova-analyst-a2a.SendMessage`: **the A2A call**.
   - `invoke_agent caldova-logistics-analyst:<version>` and `invoke_agent Caldova Logistics Analyst`: the hosted agent, with `chat gpt-54-mini` and `execute_tool get_inventory_position` etc. **inside the same trace**.
   - `execute_tool file_search.msearch`: the policy lookup.

**Say:**
> "One request, one trace: from my laptop, through both agents and every tool call. That's what you need to debug, audit and improve agents in production."

### 3.6 Monitor and quality scores (≈1–2 min) — *Tab 3*

**Do:**
1. In the Foundry portal, open **Build → caldova-coordinator → Monitor**. Set the time range to **Last 24 hours**.
2. Point out the **operational metrics**: token usage, latency and run success rate. Foundry collects these automatically for both agents.
3. Scroll to the **evaluation** charts: **Task adherence**, **Intent resolution** and **Coherence**. They come from a **continuous evaluation** rule that scores the Coordinator's live responses, with GPT-5.4-mini as the judge. Scores from today's runs may not be there yet; they arrive in batches, up to a few hours later.
4. If **Task adherence** shows some failures, use it as a talking point. They come from the Coordinator's short *delegate* step: its instructions say "summarize, don't recommend", and its summary usually names the best option anyway. The *policy* step, which writes the real answer, passes.
5. Optional: show the Analyst's quality scores under **Build → Evaluations**: the evaluation run on **caldova-logistics-analyst** with dataset **caldova-analyst-scenarios**.

**Say:**
> "Beyond traces, Foundry continuously grades the agent's answers in production: did it follow the task, understand the intent, and answer coherently? It even caught our delegate step going beyond its brief. If quality drops, you'd see it here and could set an alert."

> **Why does the Analyst use an evaluation run instead?** Foundry's continuous-evaluation rules currently support prompt agents, not hosted agents. The same evaluators score the hosted Analyst through an agent evaluation run instead.

---

## 4. Wrap-up (≈30 s)

Three takeaways:
1. **Mix and match agents:** a no-code *prompt agent* and a *code-first LangGraph agent*, each on its own model.
2. **Agents collaborate natively:** A2A connects them inside Foundry, with keyless (Entra ID) security.
3. **Full observability:** every tool call and model call is traceable, and answer quality is scored continuously.

---

## 5. Likely questions

| Question | Short answer |
|---|---|
| *What's the difference between the two agents?* | The **prompt agent** is configured in Foundry: instructions, model and tools, no code. The **hosted agent** is your own code (LangGraph here), packaged as a container that Foundry runs and scales. |
| *How do the agents talk?* | Through **A2A** (agent-to-agent protocol). Foundry exposes the hosted agent as an A2A endpoint, and the Coordinator calls it with its own agent identity. |
| *Why two Coordinator steps?* | The Coordinator first delegates (its A2A step), then reviews policy with File Search before answering. With both tools offered at once, the model sometimes kept searching instead of delegating, so separate steps make the run reliable. |
| *Are there API keys?* | Not for the agents, models or tools. They use **Microsoft Entra ID**: the presenter's sign-in, the project's managed identity, and per-agent identities with RBAC. Only telemetry uses Application Insights' standard ingestion connection string, which is stored in the Foundry project. |
| *Where does the data come from?* | It's all fictitious: policy PDFs (indexed for File Search) and synthetic ERP data inside the Analyst's container. |
| *Can I use other models, e.g. Claude?* | Yes. The Analyst supports Claude Opus 5.5 where the subscription can buy Azure Marketplace models; otherwise it uses GPT-5.4-mini. |
| *Is the app deployed to the cloud?* | No. The UI and backend run locally. Only the agents, models, tools and monitoring run in Azure. |
| *How is answer quality measured?* | Built-in Foundry evaluators (task adherence, intent resolution, coherence) score responses, with a small model as the judge. For the prompt agent this runs continuously on live traffic; for the hosted agent it runs as an evaluation over the demo scenarios. |
| *What does evaluation cost?* | A few extra GPT-5.4-mini calls per scored response. The rule is capped at 30 runs per hour. |
| *Why does Task adherence show failures?* | They come from the Coordinator's delegate step, whose summary goes slightly beyond its "don't recommend yet" instruction. Spotting that kind of drift is what continuous evaluation is for; tightening that step's instructions would fix it. |

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Badge is not **Agents connected** | Click the badge to re-check. If it stays red, sign in again (with `AZURE_CONFIG_DIR` set if `.env` uses it, see section 1) and restart `.\start.ps1`. |
| `start.ps1` fails because port 8000 is in use | The app is already running. Open **http://127.0.0.1:8000**. |
| First run is slow or times out | The hosted agent was cold-starting. Run again; the next run is faster. |
| `RateLimitError` / 429 in the trail | Model throughput was exceeded. Wait about 60 seconds and run again. |
| The A2A delegation fails | Switch to **Backend orchestrated** mode. It uses the same two agents without A2A. |
| The trace isn't found in the portal | Ingestion takes 2–5 minutes. Use the warm-up trace ID, or search for a **response ID** (shown after a run). |
| **Monitor** charts are empty | Widen the time range (for example, **Last 24 hours**) and refresh. Operational metrics take about 10–15 minutes to appear after new traffic. |
| No **evaluation** scores on Monitor | Scores are processed in batches, from 15 minutes to a few hours after a run, and only for the Coordinator. Check **Monitor → ⚙ settings** that the continuous evaluation for **caldova-coordinator** is listed and enabled, and run the scenarios earlier next time. |
| An evaluation started from a script fails with *"Getting user token was not successful"* | Start it from the Foundry portal (**Build → Evaluations → Create**). Script-started evaluations need a user token that Foundry can exchange; your portal session provides one. |
| The page looks stuck | Refresh the browser. Each run is independent. |

---

## 7. After the demo

- Stop the app with **Ctrl+C** in the terminal where `start.ps1` is running, or close that terminal.
- To remove all Azure resources: `.\scripts\teardown.ps1`.
