# Caldova | Inventory Reallocation & Safety-Stock Guidelines

Fictitious demo data. Inventory, locations, demand and allocation rules are invented for this demonstration. Effective date: 2026-11-03. Units are individual garments.

## Location priorities

Protect the four flagships first: Madrid Gran Via, Barcelona Passeig de Gracia, Paris Rivoli and Milan Corso Vittorio Emanuele. The ERP records preserve accented display names where appropriate. DC Guadalajara is the main EU hub; DC Lyon supports France and Italy.

Regular donor stores are Madrid Plenilunio, Barcelona Diagonal, Paris Belleville and Milan Bicocca. Transfer candidates are recommendations only; Regional Planning authorizes releases.

## Safety-stock rules

Keep the fixed per-SKU DC reserve below untouched. A regular donor store must retain at least seven days of its effective daily demand at dispatch. A flagship must not donate to another location in this demo. A zero-demand DC has no meaningful days-of-cover figure; report it as not applicable, not infinite.

| SKU | Guadalajara reserve | Lyon reserve |
| --- | --- | --- |
| CV-LIN-0042 | 600 | 300 |
| CV-SAT-0117 | 500 | 250 |
| CV-DEN-0203 | 400 | 200 |
| CV-COT-0088 | 500 | 250 |

Use spiked demand for satin donor protection: +300% means baseline multiplied by four. A store already below seven days of effective cover cannot donate. Retaining seven days at dispatch does not guarantee seven days of cover throughout the following weeks; flag the need to replenish donors.

## Releasable stock

Releasable units at a location are its current stock minus the protected DC reserve (for DCs) or minus seven days of effective demand (for regular donor stores). Current stock, demand forecasts and releasable quantities per location are held in the ERP system; the Logistics Analyst computes them for each incident. Flagship demand forecasts already contain the campaign demand assumption.

## Transfer execution and costing

Direct emergency transfers arrive at receiving stores after one calendar day, for EUR 0.65 per unit. They do not consume the fixed DC reserve. The scenario pools available stock across both DCs and eligible regular stores, then distributes it proportionally to flagship effective daily demand, with deterministic integer rounding.

All expedited, backup and residual-sea replenishment is also allocated proportionally to flagship demand in the illustrative recovery calculation. This is a flagship-only service comparison, not a full network optimization or a binding allocation plan. Regular stores' later replenishment and regional lane constraints remain outside the calculation.

## Forecast interpretation

The snapshot covers 2026-11-03 through 2026-12-02 inclusive. Daily demand is constant within that window for simplicity; satin is four times baseline for the entire window. There is no automatic baseline reversion.

Receipts arrive before that day's sales. A stockout day is any calendar day on which a flagship cannot satisfy its full daily demand; partial shortages count as one day. Unmet demand is lost, not backordered. Days of cover equals current stock divided by effective daily demand.

Evaluate from 2026-11-03 through the later of launch day and the day before the revised shipment becomes available in stores. Include the residual original shipment if it arrives during that window. Air-diverted units must be subtracted from the sea balance, preventing double counting.
