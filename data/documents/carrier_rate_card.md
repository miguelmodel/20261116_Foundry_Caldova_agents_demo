# Caldova | Carrier Rate Card

Fictitious demo data. Carriers, quotes, lead times and capacities below are invented planning values, not live transport offers. Effective date: 2026-11-03. Rates are EUR per garment, excluding tax.

## Standard lane estimates

| Mode and lane | Rate EUR/unit | Lead time to DC | Capacity / condition |
| --- | --- | --- | --- |
| Sea: Chittagong - Valencia - Guadalajara | 1.20 | 16 calendar days | Existing PO quantity |
| Air: Chittagong - Madrid - EU DC | 9.80 | 4 calendar days | Maximum 3,000 diverted units |
| Sea: Ningbo - Valencia - EU DC | 1.10 | 14 calendar days | Existing PO quantity |
| Air: Ningbo - Madrid - EU DC | 10.50 | 4 calendar days | Maximum 3,000 diverted units |
| Road: Porto - EU DC | 0.65 | Included in Boreal's 6-day lead | Subject to Boreal SKU capacity |
| Road: EU DC - store | 0.65 | 1 calendar day | Available recovery quantity |
| Emergency direct DC/store reallocation | 0.65 | 1 calendar day to store | Only releasable existing stock |

The sea estimates include handling to the DC. They are illustrative fast planning lanes for this demo; they are not claims about real-world ocean schedules. Sea disruptions extend the original promised DC date; do not add sea transit a second time to an already revised ETA.

## Arrival dates for an immediate decision

Air booked and released on the decision day reaches an EU DC four days later and stores one day after that. Reallocation reaches stores the day after release. A Boreal order accepted on the decision day reaches the DC six days later and stores one day after that.

Shipment-specific original and revised arrival dates are recorded in the ERP system, not in this rate card. Sea disruptions extend the original promised DC date.

## Incremental-cost rules

Original purchase, sea freight and final-mile budgets are already committed. A fully credited unused sea leg makes the air premium EUR 8.60 per diverted unit for Chittagong, or EUR 9.40 for Ningbo. The original final-mile budget follows diverted units. No second goods purchase is incurred for diversion.

An additional Boreal order incurs full goods cost, EUR 0.65 per unit inbound road and EUR 0.65 per unit final mile. A direct reallocation costs EUR 0.65 per unit once, not once per intermediate hub.

## Quote limitations

- Split quantities are integer units; at most 3,000 units can be diverted from one affected shipment.
- Air availability assumes origin access, quality release, capacity and unused sea credit are confirmed.
- Road costs are flat planning estimates across the named EU locations; route distance is not modeled.
- No customs surcharge, tax, inventory carrying cost, carbon charge, markdown or lost-margin valuation is included.
- A supplier credit is a separate potential recovery, not a freight discount or booking budget.

Logistics must obtain a firm quote and human approval before execution. The Analyst can compare these read-only scenario values but cannot reserve or purchase transport.
